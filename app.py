# app.py
import os
from flask import Flask, render_template, redirect, url_for, request, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from datetime import datetime
from config import Config

# --- App setup ---
app = Flask(__name__)
app.config.from_object(Config)

# Ensure GENERATED_FOLDER exists (config already creates it, but be defensive)
os.makedirs(app.config.get("GENERATED_FOLDER", os.path.join(os.path.abspath(os.path.dirname(__file__)), "generated")), exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ---------------- Models ----------------
class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # hashed
    role = db.Column(db.String(50), nullable=False)  # student, class_teacher, hod, principal
    signature = db.Column(db.String(200))  # optional path to signature image

class ApprovalRequest(db.Model):
    __tablename__ = "approval_request"
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

# ---------------- Login loader ----------------
@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

# ---------------- Routes ----------------
@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "error")
    # login.html is the simple login template you provided
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    # Optional search param 'q' - filters title/creator
    q = request.args.get("q", "").strip().lower()

    # Base query depending on role
    if current_user.role == "student":
        base_q = ApprovalRequest.query.filter_by(created_by=current_user.id)
    elif current_user.role == "class_teacher":
        base_q = ApprovalRequest.query
    elif current_user.role == "hod":
        base_q = ApprovalRequest.query.filter(ApprovalRequest.class_teacher_status == "Approved")
    elif current_user.role == "principal":
        base_q = ApprovalRequest.query.filter(ApprovalRequest.hod_status == "Approved")
    else:
        base_q = ApprovalRequest.query

    # Apply search if present
    if q:
        # Simple filter on title or creator username (JOIN needed — use relationship)
        base_q = base_q.join(User, ApprovalRequest.created_by == User.id).filter(
            (ApprovalRequest.title.ilike(f"%{q}%")) | (User.username.ilike(f"%{q}%"))
        )

    requests_q = base_q.order_by(ApprovalRequest.created_at.desc()).all()

    # Compute some KPIs for dashboard card
    total = len(requests_q)
    pending_principal = len([r for r in requests_q if r.principal_status == "Pending"])
    approved_final = len([r for r in requests_q if r.principal_status == "Approved"])

    kpis = {
        "total_requests": total,
        "pending_final": pending_principal,
        "approved_final": approved_final
    }

    return render_template("dashboard.html", requests=requests_q, kpis=kpis)


@app.route("/new_request", methods=["GET", "POST"])
@login_required
def new_request():
    if current_user.role != "student":
        flash("Only students can create requests.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("content") or request.form.get("description", "")
        description = description.strip() if description else ""

        if not title or not description:
            flash("Title and content are required.", "error")
            return render_template("new_request.html")

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
        flash("Request submitted successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("new_request.html")


@app.route("/request/<int:req_id>")
@login_required
def view_request(req_id):
    req = ApprovalRequest.query.get_or_404(req_id)

    # Student restrictions
    if current_user.role == "student" and req.created_by != current_user.id:
        flash("You can only view your own requests.", "error")
        return redirect(url_for("dashboard"))

    # HOD can view only after CT approved
    if current_user.role == "hod" and req.class_teacher_status == "Pending":
        flash("Not authorized - request not forwarded to HOD yet.", "error")
        return redirect(url_for("dashboard"))

    # Principal can view only after HOD approved
    if current_user.role == "principal" and req.hod_status == "Pending":
        flash("Not authorized - request not forwarded to Principal yet.", "error")
        return redirect(url_for("dashboard"))

    return render_template("request_view.html", req=req)


# ---------------- Approval Handling ----------------
@app.route('/approve/<int:req_id>', methods=['POST'])
@login_required
def approve_request(req_id):
    req = ApprovalRequest.query.get_or_404(req_id)
    role_approved = False

    try:
        if current_user.role == "class_teacher" and req.class_teacher_status == "Pending":
            req.class_teacher_status = "Approved"
            req.status = "Pending HOD"
            role_approved = True

        elif current_user.role == "hod" and req.class_teacher_status == "Approved" and req.hod_status == "Pending":
            req.hod_status = "Approved"
            req.status = "Pending Principal"
            role_approved = True

        elif current_user.role == "principal" and req.hod_status == "Approved" and req.principal_status == "Pending":
            req.principal_status = "Approved"
            req.status = "Approved"
            role_approved = True

            # -------- Generate PDF only on FINAL approval --------
            try:
                os.makedirs(app.config["GENERATED_FOLDER"], exist_ok=True)
                pdf_path = os.path.join(app.config["GENERATED_FOLDER"], f"request_{req.id}.pdf")
                c = canvas.Canvas(pdf_path, pagesize=A4)
                width, height = A4

                # Header
                c.setFont("Helvetica-Bold", 16)
                c.drawString(50, height - 50, "College eApproval - Signed Request")
                c.setFont("Helvetica", 12)
                c.drawString(50, height - 80, f"Title: {req.title}")
                c.drawString(50, height - 100, f"Submitted by: {req.creator.username} (ID: {req.created_by})")
                c.drawString(50, height - 120, f"Final Status: {req.status}")
                c.line(50, height - 130, width - 50, height - 130) # divider line

                # Body
                text = c.beginText(50, height - 160)
                text.setFont("Helvetica", 12)
                text.setLeading(16)
                for line in req.description.splitlines():
                    text.textLine(line)
                c.drawText(text)

                last_y_position = text.getY()
                # Draw signatures at bottom
                draw_signatures(c, last_y_position, height)

                c.showPage()
                c.save()
            except Exception as e:
                print(f"Error generating PDF: {e}")
                flash(f"Approved, but PDF generation failed: {e}", "error")

        else:
            flash("You are not authorized to approve this request at this time.", "error")
            return redirect(url_for("dashboard"))

        if role_approved:
            db.session.commit()
            flash("Approval updated successfully!", "success")

    except Exception as e:
        db.session.rollback()
        print(f"Error during approve flow: {e}")
        flash("An error occurred while updating approval. Try again.", "error")

    return redirect(url_for("dashboard"))


def draw_signatures(c, last_y_position, height):
    # Signatures are expected at static/signatures/<role>.png
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

    y = 120  # fixed bottom position
    x_positions = [80, 250, 420]  # spacing

    for (role_key, (label, img_path)), x in zip(roles_map.items(), x_positions):
        # Draw signature image if present
        if os.path.exists(img_path):
            try:
                img = ImageReader(img_path)
                c.drawImage(img, x, y + 20, width=120, height=50, mask="auto")
            except Exception as e:
                print(f"Error adding {label} signature image: {e}")
                c.drawString(x, y + 40, "[Signature Error]")
        else:
            c.setFont("Helvetica-Oblique", 10)
            c.setFillColorRGB(1, 0, 0)
            c.drawString(x, y + 40, "[SIGNATURE MISSING]")
            print(f"Missing signature file: {img_path}")

        # Draw role label
        c.setFont("Helvetica-Bold", 11)
        c.setFillColorRGB(0, 0, 1)
        c.drawString(x, y - 10, label.upper())

    c.setFillColorRGB(0, 0, 0)


@app.route("/reject/<int:req_id>/<role>", methods=["POST"])
@login_required
def reject_request(req_id, role):
    req = ApprovalRequest.query.get_or_404(req_id)
    role_rejected = False

    try:
        if role == "class_teacher" and current_user.role == "class_teacher" and req.class_teacher_status == "Pending":
            req.class_teacher_status = "Rejected"
            req.status = "Rejected by Class Teacher"
            role_rejected = True

        elif role == "hod" and current_user.role == "hod" and req.class_teacher_status == "Approved" and req.hod_status == "Pending":
            req.hod_status = "Rejected"
            req.status = "Rejected by HOD"
            role_rejected = True

        elif role == "principal" and current_user.role == "principal" and req.hod_status == "Approved" and req.principal_status == "Pending":
            req.principal_status = "Rejected"
            req.status = "Rejected by Principal"
            role_rejected = True

        else:
            flash("You are not authorized to reject this request at this time.", "error")
            return redirect(url_for("dashboard"))

        if role_rejected:
            db.session.commit()
            flash("Request rejected.", "success")

    except Exception as e:
        db.session.rollback()
        print(f"Error during reject flow: {e}")
        flash("An error occurred while rejecting. Try again.", "error")

    return redirect(url_for("dashboard"))


@app.route("/generate_pdf/<int:req_id>")
@login_required
def generate_pdf(req_id):
    req = ApprovalRequest.query.get_or_404(req_id)
    pdf_name = f"request_{req.id}.pdf"
    pdf_path = os.path.join(app.config["GENERATED_FOLDER"], pdf_name)

    # Authorization: students only their own; staff allowed
    if current_user.role == 'student' and req.created_by != current_user.id:
        flash("You are not authorized to view this file.", "error")
        return redirect(url_for("dashboard"))

    # Check approval status
    if req.principal_status != "Approved":
        flash("PDF not available until Principal approves.", "error")
        return redirect(url_for("view_request", req_id=req_id))

    # Check if file exists
    if not os.path.exists(pdf_path):
        flash("PDF file not found. It may not have been generated correctly.", "error")
        return redirect(url_for("view_request", req_id=req_id))

    return send_from_directory(app.config["GENERATED_FOLDER"], pdf_name, as_attachment=True)


# ------------- Main -------------
if __name__ == "__main__":
    # Create folders & DB
    os.makedirs(app.config.get("GENERATED_FOLDER", "generated"), exist_ok=True)
    os.makedirs(os.path.join(app.config.get("BASE_DIR", "."), "static", "signatures"), exist_ok=True)

    with app.app_context():
        db.create_all()

        # Helper to create user only if they don't exist
        def create_user(username, password, role):
            if not User.query.filter_by(username=username).first():
                db.session.add(User(username=username, password=generate_password_hash(password), role=role))

        create_user("student1", "studentpass", "student")
        create_user("classteacher1", "classteacherpass", "class_teacher")
        create_user("hod1", "hodpass", "hod")
        create_user("principal1", "principalpass", "principal")

        db.session.commit()

    print("--- Sample Users ---")
    print("student1 / studentpass")
    print("classteacher1 / classteacherpass")
    print("hod1 / hodpass")
    print("principal1 / principalpass")
    print("--------------------")
    print("🔥 App running (development) on http://127.0.0.1:5000")
    app.run(debug=True)
