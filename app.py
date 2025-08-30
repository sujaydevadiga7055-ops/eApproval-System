# app.py
from flask import Flask, render_template, redirect, url_for, request, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from datetime import datetime
import os
from config import Config  # you already have this file

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ---------------- Models ----------------
class User(UserMixin, db.Model):
    _tablename_ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # hashed
    role = db.Column(db.String(50), nullable=False)  # student, class_teacher, hod, principal
    signature = db.Column(db.String(200))  # path to signature image (optional)


class ApprovalRequest(db.Model):
    _tablename_ = "approval_request"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)

    status = db.Column(db.String(100), default="Pending Class Teacher")

    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    creator = db.relationship("User", backref="requests", foreign_keys=[created_by])

    class_teacher_status = db.Column(db.String(20), default="Pending")
    hod_status = db.Column(db.String(20), default="Pending")
    principal_status = db.Column(db.String(20), default="Pending")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------------- Routes ----------------
@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "student":
        requests_q = ApprovalRequest.query.filter_by(created_by=current_user.id).order_by(ApprovalRequest.created_at.desc()).all()
    elif current_user.role == "class_teacher":
        requests_q = ApprovalRequest.query.filter_by(class_teacher_status="Pending").order_by(ApprovalRequest.created_at.desc()).all()
    elif current_user.role == "hod":
        requests_q = ApprovalRequest.query.filter_by(class_teacher_status="Approved", hod_status="Pending").order_by(ApprovalRequest.created_at.desc()).all()
    elif current_user.role == "principal":
        requests_q = ApprovalRequest.query.filter_by(class_teacher_status="Approved", hod_status="Approved", principal_status="Pending").order_by(ApprovalRequest.created_at.desc()).all()
    else:
        requests_q = []
    return render_template("dashboard.html", requests=requests_q)


@app.route("/new_request", methods=["GET", "POST"])
@login_required
def new_request():
    if current_user.role != "student":
        flash("Only students can create requests.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("content") or request.form.get("description", "")
        if not title or not description.strip():
            flash("Title and content are required.", "error")
            return render_template("request_new.html")
        req = ApprovalRequest(
            title=title,
            description=description,
            created_by=current_user.id,
            status="Pending Class Teacher",
            class_teacher_status="Pending",
            hod_status="Pending",
            principal_status="Pending",
        )
        db.session.add(req)
        db.session.commit()
        flash("Request submitted!", "success")
        return redirect(url_for("dashboard"))
    return render_template("request_new.html")


@app.route("/request/<int:req_id>")
@login_required
def view_request(req_id):
    req = ApprovalRequest.query.get_or_404(req_id)

    if current_user.role == "student":
        if req.created_by != current_user.id:
            flash("You can only view your own requests.", "error")
            return redirect(url_for("dashboard"))

    elif current_user.role == "hod":
        if req.class_teacher_status != "Approved":
            flash("Not authorized - request not forwarded to HOD yet.", "error")
            return redirect(url_for("dashboard"))

    elif current_user.role == "principal":
        if req.hod_status != "Approved":
            flash("Not authorized - request not forwarded to Principal yet.", "error")
            return redirect(url_for("dashboard"))

    return render_template("request_view.html", req=req)


# ---------------- Approval Handling ----------------
@app.route('/approve/<int:req_id>', methods=['POST'])
@login_required
def approve_request(req_id):
    # Use the correct model
    req = ApprovalRequest.query.get_or_404(req_id)

    # Update approval based on role
    if current_user.role == "class_teacher":
        req.class_teacher_status = "Approved"
        req.status = "Pending HOD"
    elif current_user.role == "hod":
        req.hod_status = "Approved"
        req.status = "Pending Principal"
    elif current_user.role == "principal":
        req.principal_status = "Approved"
        req.status = "Approved"

        # -------- Generate PDF with Signatures --------
        os.makedirs(app.config["GENERATED_FOLDER"], exist_ok=True)
        pdf_path = os.path.join(app.config["GENERATED_FOLDER"], f"request_{req.id}.pdf")
        c = canvas.Canvas(pdf_path, pagesize=A4)
        width, height = A4

        # Header
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, height - 50, "College E-Approval - Signed Request")
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 80, f"Title: {req.title}")
        c.drawString(50, height - 100, f"Submitted by: {req.creator.username} (ID: {req.created_by})")
        c.drawString(50, height - 120, f"Final Status: {req.status}")

        # Body
        text = c.beginText(50, height - 160)
        text.setFont("Helvetica", 12)
        text.setLeading(16)
        for line in req.description.splitlines():
            text.textLine(line)
        c.drawText(text)

        # Call signature section dynamically
        last_y_position = text.getY()
        draw_signatures(c, last_y_position, height)

        c.showPage()
        c.save()
    else:
        flash("You are not authorized to approve this request.", "error")
        return redirect(url_for("dashboard"))

    db.session.commit()
    flash("Approval updated successfully!", "success")
    return redirect(url_for("dashboard"))


# --- Signatures Section ---
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from flask import current_app
import os

def draw_signatures(c, last_y_position, height):
    """
    Draws CLASS TEACHER, HOD, PRINCIPAL signatures side by side at the bottom.
    - Moves to next page if there isn't enough space.
    - Labels are ALL CAPS, BLUE, BOLD.
    """

    roles_map = {
        "class_teacher": (
            "CLASS TEACHER",
            os.path.join(app.root_path, "static", "signatures", "classteacher.png"),
        ),
        "hod": (
            "HOD",
            os.path.join(app.root_path, "static", "signatures", "hod.png"),
        ),
        "principal": (
            "PRINCIPAL",
            os.path.join(app.root_path, "static", "signatures", "principal.png"),
        ),
    }

    # Force bottom positioning
    y = 120  

    # Equal spacing for A4 (width ~595pt)
    x_positions = [80, 250, 420]

    for (role_key, (label, img_path)), x in zip(roles_map.items(), x_positions):
        # Draw signature image (black ink from image itself)
        if os.path.exists(img_path):
            try:
                img = ImageReader(img_path)
                c.drawImage(img, x, y + 20, width=120, height=50, mask="auto")
            except Exception as e:
                print(f"Error adding {label} signature:", e)
        else:
            # Placeholder
            c.setFont("Helvetica-Oblique", 10)
            c.setFillColorRGB(1, 0, 0)  # red
            c.drawString(x, y + 40, "[SIGNATURE MISSING]")

        # ✅ Draw role label: ALL CAPS, BLUE, BOLD
        c.setFont("Helvetica-Bold", 11)
        c.setFillColorRGB(0, 0, 1)  # blue
        c.drawString(x, y - 10, label.upper())  # enforce CAPS just in case

    # Reset to black for rest of doc
    c.setFillColorRGB(0, 0, 0)
@app.route("/reject/<int:req_id>/<role>", methods=["POST"])
@login_required
def reject_request(req_id, role):
    req = ApprovalRequest.query.get_or_404(req_id)

    if role == "class_teacher" and current_user.role == "class_teacher":
        req.class_teacher_status = "Rejected"
        req.status = "Rejected by Class Teacher"

    elif role == "hod" and current_user.role == "hod":
        req.hod_status = "Rejected"
        req.status = "Rejected by HOD"

    elif role == "principal" and current_user.role == "principal":
        req.principal_status = "Rejected"
        req.status = "Rejected by Principal"
    else:
        flash("You are not authorized to reject this request.", "error")
        return redirect(url_for("dashboard"))

    db.session.commit()
    flash("Rejected.", "success")
    return redirect(url_for("dashboard"))


@app.route("/generate_pdf/<int:req_id>")
@login_required
def generate_pdf(req_id):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    req = ApprovalRequest.query.get_or_404(req_id)
    pdf_name = f"request_{req.id}.pdf"
    pdf_path = os.path.join(app.config["GENERATED_FOLDER"], pdf_name)

    # Check approval status
    if req.principal_status != "Approved" and not os.path.exists(pdf_path):
        flash("PDF not available until Principal approves.", "error")
        return redirect(url_for("dashboard"))

    # Generate only if needed
    if not os.path.exists(pdf_path) and req.principal_status == "Approved":
        os.makedirs(app.config["GENERATED_FOLDER"], exist_ok=True)
        c = canvas.Canvas(pdf_path, pagesize=A4)
        width, height = A4

        # -------- HEADER --------
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, height - 50, "College E-Approval - Signed Request")

        c.setFont("Helvetica", 12)
        c.drawString(50, height - 80, f"Title: {req.title}")
        c.drawString(50, height - 100, f"Submitted by: {req.creator.username} (ID: {req.created_by})")
        c.drawString(50, height - 120, f"Final Status: {req.status}")

        # -------- LETTER CONTENT --------
        text = c.beginText(50, height - 160)
        text.setFont("Helvetica", 12)
        text.setLeading(16)
        for line in req.description.splitlines():
            text.textLine(line)
        c.drawText(text)

        # Get Y position after writing the letter
        last_y_position = text.getY()

        # -------- SIGNATURE SECTION CALL --------
        draw_signatures(c, last_y_position, height)

        # Finalize PDF
        c.showPage()
        c.save()

    return send_from_directory(app.config["GENERATED_FOLDER"], pdf_name, as_attachment=True)


# ------------- Main -------------
if __name__== "__main__":
    os.makedirs(app.config.get("UPLOAD_FOLDER", "uploads"), exist_ok=True)
    os.makedirs(app.config.get("GENERATED_FOLDER", "generated"), exist_ok=True)

    with app.app_context():
        db.create_all()

        if not User.query.filter_by(username="student1").first():
            db.session.add(User(username="student1", password=generate_password_hash("studentpass"), role="student"))
        if not User.query.filter_by(username="classteacher1").first():
            db.session.add(User(username="classteacher1", password=generate_password_hash("classteacherpass"), role="class_teacher"))
        if not User.query.filter_by(username="hod1").first():
            db.session.add(User(username="hod1", password=generate_password_hash("hodpass"), role="hod"))
        if not User.query.filter_by(username="principal1").first():
            db.session.add(User(username="principal1", password=generate_password_hash("principalpass"), role="principal"))

        db.session.commit()

    print("🔥 App running at http://127.0.0.1:5000")
    app.run(debug=True)