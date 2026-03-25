from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# ── DATABASE CONNECTION ──
from urllib.parse import quote_plus
password = quote_plus(os.getenv('MYSQL_PASSWORD'))
app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{os.getenv('MYSQL_USER')}:{password}"
    f"@{os.getenv('MYSQL_HOST')}/{os.getenv('MYSQL_DB')}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ── DATABASE MODELS ──
class User(db.Model):
    __tablename__ = 'users'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(150), unique=True, nullable=False)
    password   = db.Column(db.String(255), nullable=False)
    role       = db.Column(db.Enum('admin','teacher','hod'), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class Template(db.Model):
    __tablename__ = 'template'
    id           = db.Column(db.Integer, primary_key=True)
    college_logo = db.Column(db.String(255))
    vtu_logo     = db.Column(db.String(255))
    footer_text  = db.Column(db.Text)
    updated_at   = db.Column(db.DateTime, server_default=db.func.now())

class QuestionPaper(db.Model):
    __tablename__ = 'question_papers'
    id           = db.Column(db.Integer, primary_key=True)
    teacher_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subject      = db.Column(db.String(200))
    semester     = db.Column(db.String(50))
    exam_type    = db.Column(db.String(100))
    questions    = db.Column(db.Text)
    status       = db.Column(db.Enum('draft','submitted','approved','rejected'), default='draft')
    hod_comments = db.Column(db.Text)
    pdf_path     = db.Column(db.String(255))
    created_at   = db.Column(db.DateTime, server_default=db.func.now())
    teacher      = db.relationship('User', backref='papers')

# ── HELPER ──
def valid_email(email):
    return email.endswith('@bmsit.in') or email.endswith('@bmsit')

# ── AUTH ROUTES ──
@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']
        if not valid_email(email):
            flash('Email must end with @bmsit.in', 'danger')
            return render_template('login.html')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id']   = user.id
            session['user_name'] = user.name
            session['role']      = user.role
            if user.role == 'admin':   return redirect(url_for('admin_dashboard'))
            if user.role == 'teacher': return redirect(url_for('teacher_dashboard'))
            if user.role == 'hod':     return redirect(url_for('hod_dashboard'))
        else:
            flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── ADMIN ROUTES ──
@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin': return redirect(url_for('login'))
    template = Template.query.first()
    users    = User.query.filter(User.role != 'admin').all()
    return render_template('admin_dashboard.html', template=template, users=users)

@app.route('/admin/update-template', methods=['POST'])
def update_template():
    if session.get('role') != 'admin': return redirect(url_for('login'))
    footer_text = request.form['footer_text']
    template = Template.query.first()
    if template:
        template.footer_text = footer_text
    else:
        template = Template(footer_text=footer_text)
        db.session.add(template)
    db.session.commit()
    flash('Template updated!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-user', methods=['POST'])
def add_user():
    if session.get('role') != 'admin': return redirect(url_for('login'))
    name     = request.form['name']
    email    = request.form['email'].strip().lower()
    password = generate_password_hash(request.form['password'])
    role     = request.form['role']
    if not valid_email(email):
        flash('Email must end with @bmsit.in', 'danger')
        return redirect(url_for('admin_dashboard'))
    user = User(name=name, email=email, password=password, role=role)
    db.session.add(user)
    db.session.commit()
    flash('User added successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

# ── TEACHER ROUTES ──
@app.route('/teacher')
def teacher_dashboard():
    if session.get('role') != 'teacher': return redirect(url_for('login'))
    papers = QuestionPaper.query.filter_by(teacher_id=session['user_id']).order_by(QuestionPaper.created_at.desc()).all()
    return render_template('teacher_dashboard.html', papers=papers)

@app.route('/teacher/create', methods=['GET','POST'])
def create_paper():
    if session.get('role') != 'teacher': return redirect(url_for('login'))
    template = Template.query.first()
    if request.method == 'POST':
        subject   = request.form['subject']
        semester  = request.form['semester']
        exam_type = request.form['exam_type']
        questions = request.form['questions']
        action    = request.form['action']
        status    = 'submitted' if action == 'submit' else 'draft'
        paper = QuestionPaper(
            teacher_id=session['user_id'],
            subject=subject,
            semester=semester,
            exam_type=exam_type,
            questions=questions,
            status=status
        )
        db.session.add(paper)
        db.session.commit()
        if status == 'submitted':
            generate_pdf(paper.id)
            flash('Paper submitted to HOD!', 'success')
        else:
            flash('Draft saved!', 'success')
        return redirect(url_for('teacher_dashboard'))
    return render_template('question_paper.html', template=template, paper=None)

# ── HOD ROUTES ──
@app.route('/hod')
def hod_dashboard():
    if session.get('role') != 'hod': return redirect(url_for('login'))
    papers = QuestionPaper.query.filter(
        QuestionPaper.status.in_(['submitted','approved','rejected'])
    ).order_by(QuestionPaper.created_at.desc()).all()
    return render_template('hod_dashboard.html', papers=papers)

@app.route('/hod/review/<int:paper_id>', methods=['POST'])
def review_paper(paper_id):
    if session.get('role') != 'hod': return redirect(url_for('login'))
    paper          = QuestionPaper.query.get(paper_id)
    paper.status   = request.form['decision']
    paper.hod_comments = request.form['comments']
    db.session.commit()
    flash(f'Paper {paper.status}!', 'success')
    return redirect(url_for('hod_dashboard'))

@app.route('/download/<int:paper_id>')
def download_pdf(paper_id):
    paper = QuestionPaper.query.get(paper_id)
    if paper and paper.pdf_path and os.path.exists(paper.pdf_path):
        return send_file(paper.pdf_path, as_attachment=True)
    flash('PDF not found', 'danger')
    return redirect(url_for('teacher_dashboard'))

# ── PDF GENERATION ──
def generate_pdf(paper_id):
    paper    = QuestionPaper.query.get(paper_id)
    template = Template.query.first()
    if not paper: return

    os.makedirs('generated_pdfs', exist_ok=True)
    pdf_path = f"generated_pdfs/paper_{paper_id}.pdf"

    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    # HEADER
    vtu_logo = 'static/images/vtu_logo.png'
    if os.path.exists(vtu_logo):
        c.drawImage(vtu_logo, 1*cm, height-3*cm, width=2.5*cm, height=2.5*cm, preserveAspectRatio=True)

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width/2, height-1.5*cm, "BMS INSTITUTE OF TECHNOLOGY & MANAGEMENT")
    c.setFont("Helvetica", 11)
    c.drawCentredString(width/2, height-2.2*cm, "Affiliated to Visvesvaraya Technological University, Belagavi")

    bmsit_logo = 'static/images/bmsit_logo.png'
    if os.path.exists(bmsit_logo):
        c.drawImage(bmsit_logo, width-3.5*cm, height-3*cm, width=2.5*cm, height=2.5*cm, preserveAspectRatio=True)

    c.line(1*cm, height-3.2*cm, width-1*cm, height-3.2*cm)

    # PAPER INFO
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1*cm, height-4*cm,   f"Subject: {paper.subject}")
    c.drawString(1*cm, height-4.7*cm, f"Semester: {paper.semester}    Exam Type: {paper.exam_type}")
    c.drawString(1*cm, height-5.4*cm, f"Teacher: {paper.teacher.name}")
    c.line(1*cm, height-5.7*cm, width-1*cm, height-5.7*cm)

    # QUESTIONS
    text_obj = c.beginText(1*cm, height-6.5*cm)
    text_obj.setFont("Helvetica", 11)
    for line in (paper.questions or '').split('\n'):
        text_obj.textLine(line)
    c.drawText(text_obj)

    # FOOTER
    footer = (template.footer_text if template else "BMS Institute of Technology & Management, Bengaluru - 560064")
    c.line(1*cm, 2*cm, width-1*cm, 2*cm)
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(width/2, 1.3*cm, footer)

    c.save()

    paper.pdf_path = pdf_path
    db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)