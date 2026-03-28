from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from urllib.parse import quote_plus
from datetime import datetime, timedelta
import os, json, random, string

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# ── SESSION TIMEOUT (30 minutes) ──
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

# ── DATABASE CONNECTION ──
password = quote_plus(os.getenv('MYSQL_PASSWORD'))
app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{os.getenv('MYSQL_USER')}:{password}"
    f"@{os.getenv('MYSQL_HOST')}/{os.getenv('MYSQL_DB')}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ── MAIL CONFIG ──
app.config['MAIL_SERVER']   = 'smtp.gmail.com'
app.config['MAIL_PORT']     = 587
app.config['MAIL_USE_TLS']  = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_EMAIL')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
mail = Mail(app)

# ── DATABASE MODELS ──
class User(db.Model):
    __tablename__    = 'users'
    id               = db.Column(db.Integer, primary_key=True)
    name             = db.Column(db.String(100), nullable=False)
    email            = db.Column(db.String(150), unique=True, nullable=False)
    password         = db.Column(db.String(255), nullable=False)
    role             = db.Column(db.Enum('admin','teacher','hod'), nullable=False)
    otp              = db.Column(db.String(6))
    otp_expiry       = db.Column(db.DateTime)
    failed_attempts  = db.Column(db.Integer, default=0)
    is_blocked       = db.Column(db.Boolean, default=False)
    created_at       = db.Column(db.DateTime, server_default=db.func.now())

class Template(db.Model):
    __tablename__ = 'template'
    id           = db.Column(db.Integer, primary_key=True)
    college_logo = db.Column(db.String(255))
    vtu_logo     = db.Column(db.String(255))
    footer_text  = db.Column(db.Text)
    updated_at   = db.Column(db.DateTime, server_default=db.func.now())

class QuestionPaper(db.Model):
    __tablename__ = 'question_papers'
    id            = db.Column(db.Integer, primary_key=True)
    teacher_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subject       = db.Column(db.String(200))
    course_code   = db.Column(db.String(50))
    department    = db.Column(db.String(100))
    academic_year = db.Column(db.String(20))
    semester      = db.Column(db.String(50))
    exam_type     = db.Column(db.String(100))
    duration      = db.Column(db.String(50))
    max_marks     = db.Column(db.Integer, default=0)
    questions     = db.Column(db.Text)
    status        = db.Column(db.Enum('draft','submitted','approved','rejected'), default='draft')
    hod_comments  = db.Column(db.Text)
    pdf_path      = db.Column(db.String(255))
    created_at    = db.Column(db.DateTime, server_default=db.func.now())
    teacher       = db.relationship('User', backref='papers')

# ── HELPERS ──
def valid_email(email):
    return email.endswith('@bmsit.in') or email.endswith('@bmsit')

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def send_email(to, subject, body):
    try:
        msg = Message(subject,
                      sender=os.getenv('MAIL_EMAIL'),
                      recipients=[to])
        msg.body = body
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

# ── SESSION TIMEOUT ──
@app.before_request
def check_session_timeout():
    if 'user_id' in session:
        session.permanent = True

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

        if not user:
            flash('Invalid email or password', 'danger')
            return render_template('login.html')

        # Check if blocked
        if user.is_blocked:
            flash('Your account is blocked due to too many failed attempts. Contact admin.', 'danger')
            return render_template('login.html')

        if check_password_hash(user.password, password):
            # Reset failed attempts on success
            user.failed_attempts = 0
            db.session.commit()

            # Generate OTP
            otp = generate_otp()
            user.otp        = otp
            user.otp_expiry = datetime.now() + timedelta(minutes=5)
            db.session.commit()

            # Send OTP email
            send_email(
    os.getenv('MAIL_EMAIL'),
    'QPMS Login OTP',
    f'Hello {user.name},\n\nYour OTP for QPMS login is: {otp}\n\nThis OTP is valid for 5 minutes.\n\nDo not share this OTP with anyone.'
)
            # Store temp session for OTP verification
            session['otp_user_id'] = user.id
            flash('OTP sent to your email!', 'success')
            return redirect(url_for('verify_otp'))
        else:
            # Wrong password
            user.failed_attempts += 1
            if user.failed_attempts >= 3:
                user.is_blocked = True
                db.session.commit()
                flash('Account blocked after 3 failed attempts. Contact admin.', 'danger')
            else:
                db.session.commit()
                remaining = 3 - user.failed_attempts
                flash(f'Wrong password! {remaining} attempt(s) remaining.', 'danger')

    return render_template('login.html')

@app.route('/verify-otp', methods=['GET','POST'])
def verify_otp():
    if 'otp_user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        entered_otp = request.form['otp'].strip()
        user = User.query.get(session['otp_user_id'])

        if not user:
            return redirect(url_for('login'))

        if datetime.now() > user.otp_expiry:
            flash('OTP expired! Please login again.', 'danger')
            session.pop('otp_user_id', None)
            return redirect(url_for('login'))

        if entered_otp == user.otp:
            # Clear OTP
            user.otp        = None
            user.otp_expiry = None
            db.session.commit()

            # Set full session
            session.pop('otp_user_id', None)
            session['user_id']   = user.id
            session['user_name'] = user.name
            session['role']      = user.role
            session.permanent    = True

            if user.role == 'admin':   return redirect(url_for('admin_dashboard'))
            if user.role == 'teacher': return redirect(url_for('teacher_dashboard'))
            if user.role == 'hod':     return redirect(url_for('hod_dashboard'))
        else:
            flash('Invalid OTP! Please try again.', 'danger')

    return render_template('verify_otp.html')

@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()

        if not valid_email(email):
            flash('Email must end with @bmsit.in', 'danger')
            return render_template('forgot_password.html')

        user = User.query.filter_by(email=email).first()
        if user:
            otp = generate_otp()
            user.otp        = otp
            user.otp_expiry = datetime.now() + timedelta(minutes=10)
            db.session.commit()
            send_email(
                user.email,
                'QPMS Password Reset OTP',
                f'Hello {user.name},\n\nYour OTP for password reset is: {otp}\n\nThis OTP is valid for 10 minutes.\n\nIf you did not request this, ignore this email.'
            )
        flash('If your email exists, an OTP has been sent!', 'success')
        session['reset_email'] = email
        return redirect(url_for('reset_password'))

    return render_template('forgot_password.html')

@app.route('/reset-password', methods=['GET','POST'])
def reset_password():
    if 'reset_email' not in session:
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        otp          = request.form['otp'].strip()
        new_password = request.form['new_password']
        confirm      = request.form['confirm_password']

        if new_password != confirm:
            flash('Passwords do not match!', 'danger')
            return render_template('reset_password.html')

        user = User.query.filter_by(email=session['reset_email']).first()

        if not user:
            flash('User not found!', 'danger')
            return redirect(url_for('forgot_password'))

        if datetime.now() > user.otp_expiry:
            flash('OTP expired! Please try again.', 'danger')
            return redirect(url_for('forgot_password'))

        if otp == user.otp:
            user.password       = generate_password_hash(new_password)
            user.otp            = None
            user.otp_expiry     = None
            user.failed_attempts = 0
            user.is_blocked     = False
            db.session.commit()
            session.pop('reset_email', None)
            flash('Password reset successfully! Please login.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Invalid OTP!', 'danger')

    return render_template('reset_password.html')

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

@app.route('/admin/unblock/<int:user_id>')
def unblock_user(user_id):
    if session.get('role') != 'admin': return redirect(url_for('login'))
    user = User.query.get(user_id)
    if user:
        user.is_blocked     = False
        user.failed_attempts = 0
        db.session.commit()
        flash(f'{user.name} unblocked successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

# ── TEACHER ROUTES ──
@app.route('/teacher')
def teacher_dashboard():
    if session.get('role') != 'teacher': return redirect(url_for('login'))
    papers = QuestionPaper.query.filter_by(
        teacher_id=session['user_id']
    ).order_by(QuestionPaper.created_at.desc()).all()
    return render_template('teacher_dashboard.html', papers=papers)

@app.route('/teacher/create', methods=['GET','POST'])
def create_paper():
    if session.get('role') != 'teacher': return redirect(url_for('login'))
    template = Template.query.first()
    if request.method == 'POST':
        subject       = request.form['subject']
        course_code   = request.form['course_code']
        department    = request.form['department']
        academic_year = request.form['academic_year']
        semester      = request.form['semester']
        exam_type     = request.form['exam_type']
        duration      = request.form['duration']
        max_marks     = request.form.get('max_marks', 0)
        questions     = request.form['questions']
        action        = request.form['action']
        status        = 'submitted' if action == 'submit' else 'draft'
        paper = QuestionPaper(
            teacher_id    = session['user_id'],
            subject       = subject,
            course_code   = course_code,
            department    = department,
            academic_year = academic_year,
            semester      = semester,
            exam_type     = exam_type,
            duration      = duration,
            max_marks     = int(max_marks) if max_marks else 0,
            questions     = questions,
            status        = status
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
    for paper in papers:
        try:
            paper.parsed_questions = json.loads(paper.questions)
        except:
            paper.parsed_questions = None
    return render_template('hod_dashboard.html', papers=papers)

@app.route('/hod/review/<int:paper_id>', methods=['POST'])
def review_paper(paper_id):
    if session.get('role') != 'hod': return redirect(url_for('login'))
    paper              = QuestionPaper.query.get(paper_id)
    paper.status       = request.form['decision']
    paper.hod_comments = request.form['comments']
    db.session.commit()

    # Send email notification to teacher
    teacher = User.query.get(paper.teacher_id)
    if teacher:
        status_word = 'APPROVED ✅' if paper.status == 'approved' else 'REJECTED ❌'
        send_email(
            teacher.email,
            f'QPMS - Your Question Paper has been {paper.status.capitalize()}',
            f'Hello {teacher.name},\n\n'
            f'Your question paper for {paper.subject} ({paper.exam_type}) '
            f'has been {status_word} by the HOD.\n\n'
            f'HOD Comments: {paper.hod_comments or "No comments"}\n\n'
            f'Please login to QPMS to view and download your paper.\n\n'
            f'Regards,\nBMSIT Question Paper Management System'
        )

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

    # ── HEADER ──
    vtu_logo = 'static/images/vtu_logo.png'
    if os.path.exists(vtu_logo):
        c.drawImage(vtu_logo, 1*cm, height-3.2*cm,
                    width=2.5*cm, height=2.5*cm, preserveAspectRatio=True)

    bmsit_logo = 'static/images/bmsit_logo.png'
    if os.path.exists(bmsit_logo):
        c.drawImage(bmsit_logo, width-3.5*cm, height-3.2*cm,
                    width=2.5*cm, height=2.5*cm, preserveAspectRatio=True)

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width/2, height-1.5*cm,
                        "BMS INSTITUTE OF TECHNOLOGY & MANAGEMENT")
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height-2.1*cm,
                        "Affiliated to Visvesvaraya Technological University, Belagavi")
    c.drawCentredString(width/2, height-2.6*cm,
                        "Yelahanka, Bengaluru - 560064")

    c.setLineWidth(1.5)
    c.line(1*cm, height-3.4*cm, width-1*cm, height-3.4*cm)

    # ── PAPER INFO ──
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1*cm,    height-4.1*cm, f"Subject       : {paper.subject}")
    c.drawString(1*cm,    height-4.7*cm, f"Course Code   : {paper.course_code or '-'}")
    c.drawString(1*cm,    height-5.3*cm, f"Department    : {paper.department or '-'}")
    c.drawString(1*cm,    height-5.9*cm, f"Academic Year : {paper.academic_year or '-'}")
    c.drawString(width/2, height-4.1*cm, f"Exam Type : {paper.exam_type}")
    c.drawString(width/2, height-4.7*cm, f"Semester  : {paper.semester}")
    c.drawString(width/2, height-5.3*cm, f"Duration  : {paper.duration or '-'}")
    c.drawString(width/2, height-5.9*cm, f"Max Marks : {paper.max_marks or '-'}")
    c.drawString(width/2, height-6.5*cm, f"Teacher   : {paper.teacher.name}")
    c.line(1*cm, height-6.9*cm, width-1*cm, height-6.9*cm)

    # ── TABLE SETUP ──
    y         = height - 7.5*cm
    margin    = 1*cm
    col_sl    = 1.2*cm
    col_marks = 2.0*cm
    col_rbtco = 2.5*cm
    col_q     = width - (2*margin) - col_sl - col_marks - col_rbtco
    x_sl      = 1*cm
    x_q       = x_sl    + col_sl
    x_marks   = x_q     + col_q
    x_rbtco   = x_marks + col_marks
    row_h     = 0.7*cm

    def draw_table_header(y):
        c.setFillColorRGB(0.23, 0.27, 0.49)
        c.rect(x_sl, y - row_h,
               col_sl + col_q + col_marks + col_rbtco,
               row_h, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x_sl + col_sl/2,       y - row_h + 0.2*cm, "Sl No.")
        c.drawString(x_q + 0.2*cm,                 y - row_h + 0.2*cm, "Question")
        c.drawCentredString(x_marks + col_marks/2, y - row_h + 0.2*cm, "Marks")
        c.drawCentredString(x_rbtco + col_rbtco/2, y - row_h + 0.2*cm, "RBT/CO")
        c.setFillColorRGB(0, 0, 0)
        return y - row_h

    def draw_row(y, sl, question_text, marks, rbtco, is_alt=False):
        if is_alt:
            c.setFillColorRGB(0.95, 0.95, 1.0)
        else:
            c.setFillColorRGB(1, 1, 1)
        c.rect(x_sl, y - row_h,
               col_sl + col_q + col_marks + col_rbtco,
               row_h, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.rect(x_sl, y - row_h,
               col_sl + col_q + col_marks + col_rbtco,
               row_h, fill=0, stroke=1)
        c.setFont("Helvetica", 9)
        c.drawCentredString(x_sl + col_sl/2,       y - row_h + 0.2*cm, str(sl))
        q_text = question_text[:90] + '...' if len(question_text) > 90 else question_text
        c.drawString(x_q + 0.2*cm,                 y - row_h + 0.2*cm, q_text)
        c.drawCentredString(x_marks + col_marks/2, y - row_h + 0.2*cm, str(marks))
        c.drawCentredString(x_rbtco + col_rbtco/2, y - row_h + 0.2*cm, str(rbtco))
        return y - row_h

    def draw_or_divider(y):
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0.23, 0.27, 0.49)
        c.drawCentredString(width/2, y - 0.4*cm, "————————— OR —————————")
        c.setFillColorRGB(0, 0, 0)
        return y - 0.8*cm

    def check_new_page(y):
        if y < 5*cm:
            c.showPage()
            return height - 1*cm
        return y

    # ── DRAW QUESTIONS ──
    try:
        questions = json.loads(paper.questions)
    except:
        questions = []

    for q in questions:
        qNum = q.get('qNum', '')

        y = check_new_page(y)
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0.23, 0.27, 0.49)
        c.drawString(1*cm, y - 0.3*cm, f"Question {qNum}")
        c.setFillColorRGB(0, 0, 0)
        y -= 0.5*cm

        y = check_new_page(y)
        y = draw_table_header(y)

        main = q.get('main', {})
        if main:
            if not main.get('hasSub'):
                y = check_new_page(y)
                rbtco = f"{main.get('rbt','-')}/{main.get('co','-')}"
                y = draw_row(y, 1, main.get('text',''), main.get('marks','-'), rbtco)
            else:
                for idx, sub in enumerate(main.get('subQuestions', [])):
                    y = check_new_page(y)
                    rbtco = f"{sub.get('rbt','-')}/{sub.get('co','-')}"
                    text  = f"{sub.get('label','')}. {sub.get('text','')}"
                    y = draw_row(y, idx+1, text, sub.get('marks','-'), rbtco)

        y = check_new_page(y)
        y = draw_or_divider(y)

        y = check_new_page(y)
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0.23, 0.27, 0.49)
        c.drawString(1*cm, y - 0.3*cm, f"Question {qNum} (Alternative)")
        c.setFillColorRGB(0, 0, 0)
        y -= 0.5*cm

        y = check_new_page(y)
        y = draw_table_header(y)

        alt = q.get('alternative', {})
        if alt:
            if not alt.get('hasSub'):
                y = check_new_page(y)
                rbtco = f"{alt.get('rbt','-')}/{alt.get('co','-')}"
                y = draw_row(y, 1, alt.get('text',''), alt.get('marks','-'), rbtco, is_alt=True)
            else:
                for idx, sub in enumerate(alt.get('subQuestions', [])):
                    y = check_new_page(y)
                    rbtco = f"{sub.get('rbt','-')}/{sub.get('co','-')}"
                    text  = f"{sub.get('label','')}. {sub.get('text','')}"
                    y = draw_row(y, idx+1, text, sub.get('marks','-'), rbtco, is_alt=True)

        y -= 0.5*cm

    # ── SIGNATURE TABLE ──
    sig_y      = 4.5*cm
    sig_w      = (width - 2*cm) / 4
    sig_x      = 1*cm
    sig_h_top  = 1.5*cm
    sig_h_bot  = 0.7*cm
    sig_labels = ['CC', 'M-H', 'HOD', 'BOC']

    c.setFont("Helvetica-Bold", 10)
    c.drawString(1*cm, sig_y + sig_h_top + sig_h_bot + 0.3*cm,
                 f"Total Marks: {paper.max_marks or '-'}")

    for i in range(4):
        c.rect(sig_x + i*sig_w, sig_y + sig_h_bot,
               sig_w, sig_h_top, fill=0, stroke=1)

    for i, label in enumerate(sig_labels):
        c.rect(sig_x + i*sig_w, sig_y,
               sig_w, sig_h_bot, fill=0, stroke=1)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(sig_x + i*sig_w + sig_w/2,
                            sig_y + 0.15*cm, label)

    # ── FOOTER ──
    footer = (template.footer_text
              if template else
              "BMS Institute of Technology & Management, Bengaluru - 560064")
    c.setLineWidth(1)
    c.line(1*cm, 1.8*cm, width-1*cm, 1.8*cm)
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(width/2, 1.2*cm, footer)

    c.save()

    paper.pdf_path = pdf_path
    db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)