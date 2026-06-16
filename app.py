from flask import Flask, render_template, redirect, url_for, flash, request, abort, Response, session
from extensions import db, login_manager
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import hashlib
import bleach
import markdown
from markupsafe import Markup
import os
import csv
from io import StringIO
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///library.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Для выполнения данного действия необходимо пройти процедуру аутентификации'

from models import User, Role, Book, Genre, Cover, Review, BookVisit

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.template_filter('markdown')
def markdown_filter(text):
    if text:
        return Markup(markdown.markdown(text))
    return ''

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    books = Book.query.order_by(Book.year.desc()).paginate(page=page, per_page=10)
    
    three_months_ago = datetime.utcnow() - timedelta(days=90)
    popular_books = db.session.query(
        Book, db.func.count(BookVisit.id).label('views')
    ).join(BookVisit, Book.id == BookVisit.book_id)\
     .filter(BookVisit.visit_time >= three_months_ago)\
     .group_by(Book.id)\
     .order_by(db.desc('views'))\
     .limit(5).all()
    
    recent_books = []
    if current_user.is_authenticated:
        recent_visits = db.session.query(
            BookVisit.book_id, 
            db.func.max(BookVisit.visit_time).label('last_visit')
        ).filter_by(user_id=current_user.id)\
         .group_by(BookVisit.book_id)\
         .order_by(db.desc('last_visit'))\
         .limit(5).all()
        
        book_ids = [v[0] for v in recent_visits]
        if book_ids:
            recent_books = Book.query.filter(Book.id.in_(book_ids)).all()
            recent_books.sort(key=lambda b: book_ids.index(b.id))
    else:
        recent_book_ids = session.get('recent_books', [])
        seen = set()
        unique_ids = []
        for bid in recent_book_ids:
            if bid not in seen:
                seen.add(bid)
                unique_ids.append(bid)
        unique_ids = unique_ids[:5]
        if unique_ids:
            recent_books = Book.query.filter(Book.id.in_(unique_ids)).all()
            recent_books.sort(key=lambda b: unique_ids.index(b.id))
    
    return render_template('index.html', 
                         books=books, 
                         popular_books=popular_books,
                         recent_books=recent_books)

@app.route('/book/<int:book_id>')
def book_detail(book_id):
    book = Book.query.get_or_404(book_id)
    
    user_review = None
    if current_user.is_authenticated:
        user_review = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
    
    if current_user.is_authenticated:
        user_id = current_user.id
        visitor_name = None
    else:
        user_id = None
        visitor_name = 'Неаутентифицированный пользователь'
        
        recent_books = session.get('recent_books', [])
        if book_id in recent_books:
            recent_books.remove(book_id)
        recent_books.insert(0, book_id)
        seen = set()
        unique = []
        for bid in recent_books:
            if bid not in seen:
                seen.add(bid)
                unique.append(bid)
        session['recent_books'] = unique[:5]
    
    today = datetime.utcnow().date()
    visits_today = BookVisit.query.filter(
        BookVisit.book_id == book_id,
        BookVisit.user_id == user_id,
        db.func.date(BookVisit.visit_time) == today
    ).count()
    
    if visits_today < 10:
        visit = BookVisit(book_id=book_id, user_id=user_id, visitor_name=visitor_name)
        db.session.add(visit)
        db.session.commit()
    
    return render_template('book_detail.html', book=book, user_review=user_review)

@app.route('/book/add', methods=['GET', 'POST'])
@login_required
def add_book():
    if current_user.role.name != 'admin':
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    from forms import BookForm
    form = BookForm()
    form.genres.choices = [(g.id, g.name) for g in Genre.query.all()]
    
    if form.validate_on_submit():
        try:
            book = Book(
                title=form.title.data,
                description=form.description.data,
                year=form.year.data,
                publisher=form.publisher.data,
                author=form.author.data,
                pages=form.pages.data
            )
            db.session.add(book)
            db.session.flush()
            
            for genre_id in form.genres.data:
                genre = Genre.query.get(genre_id)
                if genre:
                    book.genres.append(genre)
            
            if form.cover.data:
                file = form.cover.data
                filename = secure_filename(file.filename)
                file_content = file.read()
                file_md5 = hashlib.md5(file_content).hexdigest()
                
                existing_cover = Cover.query.filter_by(md5_hash=file_md5).first()
                if existing_cover:
                    existing_cover.book_id = book.id
                else:
                    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
                    
                    cover = Cover(
                        filename='temp',
                        mime_type=file.mimetype,
                        md5_hash=file_md5,
                        book_id=book.id
                    )
                    db.session.add(cover)
                    db.session.flush()
                    
                    new_filename = f"{cover.id}.{ext}"
                    cover.filename = new_filename
                    
                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                    with open(file_path, 'wb') as f:
                        f.write(file_content)
            
            db.session.commit()
            flash('Книга успешно добавлена', 'success')
            return redirect(url_for('book_detail', book_id=book.id))
            
        except Exception as e:
            db.session.rollback()
            flash('При сохранении данных возникла ошибка. Проверьте корректность введённых данных.', 'danger')
            return render_template('book_form.html', form=form, title='Добавить книгу', is_edit=False)
    
    return render_template('book_form.html', form=form, title='Добавить книгу', is_edit=False)

@app.route('/book/edit/<int:book_id>', methods=['GET', 'POST'])
@login_required
def edit_book(book_id):
    if current_user.role.name not in ['admin', 'moderator']:
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    book = Book.query.get_or_404(book_id)
    from forms import BookForm
    form = BookForm(obj=book)
    form.genres.choices = [(g.id, g.name) for g in Genre.query.all()]
    
    if form.validate_on_submit():
        try:
            book.title = form.title.data
            book.description = form.description.data
            book.year = form.year.data
            book.publisher = form.publisher.data
            book.author = form.author.data
            book.pages = form.pages.data
            book.genres = [Genre.query.get(gid) for gid in form.genres.data]
            db.session.commit()
            flash('Книга успешно обновлена', 'success')
            return redirect(url_for('book_detail', book_id=book.id))
        except Exception as e:
            db.session.rollback()
            flash('При сохранении данных возникла ошибка. Проверьте корректность введённых данных.', 'danger')
    
    form.genres.data = [g.id for g in book.genres]
    return render_template('book_form.html', form=form, title='Редактировать книгу', is_edit=True)

@app.route('/book/delete/<int:book_id>', methods=['POST'])
@login_required
def delete_book(book_id):
    if current_user.role.name != 'admin':
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    book = Book.query.get_or_404(book_id)
    
    if book.cover:
        cover_path = os.path.join(app.config['UPLOAD_FOLDER'], book.cover.filename)
        if os.path.exists(cover_path):
            os.remove(cover_path)
    
    db.session.delete(book)
    db.session.commit()
    
    flash('Книга успешно удалена', 'success')
    return redirect(url_for('index'))

@app.route('/review/add/<int:book_id>', methods=['GET', 'POST'])
@login_required
def add_review(book_id):
    book = Book.query.get_or_404(book_id)
    
    existing_review = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
    if existing_review:
        flash('Вы уже оставляли рецензию на эту книгу', 'warning')
        return redirect(url_for('book_detail', book_id=book_id))
    
    from forms import ReviewForm
    form = ReviewForm()
    
    if form.validate_on_submit():
        try:
            cleaned_text = bleach.clean(form.text.data, strip=True)
            review = Review(
                book_id=book_id,
                user_id=current_user.id,
                rating=form.rating.data,
                text=cleaned_text
            )
            db.session.add(review)
            db.session.commit()
            flash('Рецензия добавлена', 'success')
            return redirect(url_for('book_detail', book_id=book_id))
        except Exception as e:
            db.session.rollback()
            flash('При сохранении рецензии возникла ошибка.', 'danger')
    
    return render_template('review_form.html', form=form, book=book)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    from forms import LoginForm
    form = LoginForm()
    
    if form.validate_on_submit():
        user = User.query.filter_by(login=form.login.data).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            flash('Вход выполнен успешно', 'success')
            return redirect(next_page or url_for('index'))
        else:
            flash('Невозможно аутентифицироваться с указанными логином и паролем', 'danger')
    
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))

@app.route('/statistics')
@login_required
def statistics():
    if current_user.role.name != 'admin':
        flash('У вас недостаточно прав для выполнения данного действия', 'danger')
        return redirect(url_for('index'))
    
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    page_log = request.args.get('page_log', 1, type=int)
    query_log = BookVisit.query.order_by(BookVisit.visit_time.desc())
    if date_from:
        query_log = query_log.filter(BookVisit.visit_time >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        query_log = query_log.filter(BookVisit.visit_time <= datetime.strptime(date_to + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
    log_entries = query_log.paginate(page=page_log, per_page=10)
    
    page_stats = request.args.get('page_stats', 1, type=int)
    query_stats = db.session.query(
        Book, db.func.count(BookVisit.id).label('views')
    ).join(BookVisit, Book.id == BookVisit.book_id)\
     .filter(BookVisit.user_id != None)\
     .group_by(Book.id)
    if date_from:
        query_stats = query_stats.filter(BookVisit.visit_time >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        query_stats = query_stats.filter(BookVisit.visit_time <= datetime.strptime(date_to + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
    stats_books = query_stats.order_by(db.desc('views')).paginate(page=page_stats, per_page=10)
    
    return render_template('statistics.html', 
                         log_entries=log_entries, 
                         stats_books=stats_books,
                         date_from=date_from,
                         date_to=date_to)

@app.route('/export/<string:type>')
@login_required
def export_csv(type):
    if current_user.role.name != 'admin':
        flash('У вас недостаточно прав', 'danger')
        return redirect(url_for('index'))
    
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    si = StringIO()
    cw = csv.writer(si)
    
    if type == 'log':
        cw.writerow(['№', 'Пользователь', 'Название книги', 'Дата и время просмотра'])
        query = BookVisit.query.order_by(BookVisit.visit_time.desc())
        if date_from:
            query = query.filter(BookVisit.visit_time >= datetime.strptime(date_from, '%Y-%m-%d'))
        if date_to:
            query = query.filter(BookVisit.visit_time <= datetime.strptime(date_to + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
        visits = query.all()
        for idx, visit in enumerate(visits, 1):
            if visit.user:
                user_name = f"{visit.user.first_name} {visit.user.last_name}"
            elif visit.visitor_name:
                user_name = visit.visitor_name
            else:
                user_name = 'Неаутентифицированный пользователь'
            cw.writerow([idx, user_name, visit.book.title, visit.visit_time.strftime('%d.%m.%Y %H:%M:%S')])
        filename = f"journal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    else:
        cw.writerow(['№', 'Название книги', 'Количество просмотров'])
        query = db.session.query(
            Book, db.func.count(BookVisit.id).label('views')
        ).join(BookVisit, Book.id == BookVisit.book_id)\
         .filter(BookVisit.user_id != None)\
         .group_by(Book.id)
        if date_from:
            query = query.filter(BookVisit.visit_time >= datetime.strptime(date_from, '%Y-%m-%d'))
        if date_to:
            query = query.filter(BookVisit.visit_time <= datetime.strptime(date_to + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
        stats = query.order_by(db.desc('views')).all()
        for idx, (book, views) in enumerate(stats, 1):
            cw.writerow([idx, book.title, views])
        filename = f"stats_{datetime.now().strftime('%Y%m%d_H%M%S')}.csv"
    
    output = si.getvalue().encode('utf-8-sig')
    return Response(output, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if Role.query.count() == 0:
            admin_role = Role(name='admin', description='Администратор')
            moderator_role = Role(name='moderator', description='Модератор')
            user_role = Role(name='user', description='Пользователь')
            db.session.add_all([admin_role, moderator_role, user_role])
            db.session.commit()
    app.run(debug=True)