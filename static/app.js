// static/app.js
function confirmApprove(e){
  if(!confirm("Are you sure you want to APPROVE this request?")) {
    e.preventDefault();
    return false;
  }
  return true;
}
function confirmReject(e){
  if(!confirm("Are you sure you want to REJECT this request?")) {
    e.preventDefault();
    return false;
  }
  return true;
}
window.confirmApprove = confirmApprove;
window.confirmReject = confirmReject;
