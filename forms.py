from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, IntegerField, SelectMultipleField, FileField, PasswordField, SelectField, BooleanField
from wtforms.validators import DataRequired, Length, NumberRange

class BookForm(FlaskForm):
    title = StringField('Название', validators=[DataRequired()])
    description = TextAreaField('Описание (Markdown)', validators=[DataRequired()], render_kw={"required": False})
    year = IntegerField('Год', validators=[DataRequired(), NumberRange(min=0, max=2026)])
    publisher = StringField('Издательство', validators=[DataRequired()])
    author = StringField('Автор', validators=[DataRequired()])
    pages = IntegerField('Объём (страниц)', validators=[DataRequired(), NumberRange(min=1)])
    genres = SelectMultipleField('Жанры', coerce=int, validators=[DataRequired()])
    cover = FileField('Обложка')

class ReviewForm(FlaskForm):
    rating = SelectField('Оценка', choices=[
        (5, '5 – отлично'),
        (4, '4 – хорошо'),
        (3, '3 – удовлетворительно'),
        (2, '2 – неудовлетворительно'),
        (1, '1 – плохо'),
        (0, '0 – ужасно')
    ], coerce=int, validators=[DataRequired()])
    text = TextAreaField('Текст рецензии', validators=[DataRequired()], render_kw={"required": False})

class LoginForm(FlaskForm):
    login = StringField('Логин', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')