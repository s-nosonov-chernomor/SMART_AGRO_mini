# change_password.py
"""
Смена пароля — интерактивно, без параметров командной строки.
- Автопоиск БД через config.py (DB_PATH) или mini_150825.db в текущей папке.
- Рефлексия схемы. По умолчанию: таблица 'user', колонки id/username/password_hash.
- Если схема отличается — попросит выбрать таблицу и колонки из списка.
- Поиск пользователя по ID или по логину (username).
- Хэш пароля через werkzeug.generate_password_hash (совместимо с Flask-логикой).

Зависимости:
  pip install SQLAlchemy werkzeug
"""

import os
import sys
import logging
from getpass import getpass

from sqlalchemy import create_engine, Table, MetaData, select, update, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, NoSuchTableError
from werkzeug.security import generate_password_hash

# ───────────────── ЛОГИ ─────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pwchanger")

# ──────────────── ДЕФОЛТЫ ───────────────
DEFAULT_DB_FILENAME = "mini_150825.db"
DEFAULT_TABLE_PRIMARY = "user"
DEFAULT_TABLE_FALLBACK = "users"
DEFAULT_ID_COL = "id"
DEFAULT_LOGIN_COL = "username"
DEFAULT_PASS_COL = "password_hash"


def pick(prompt: str, options: list[str]) -> str:
    """Простой выбор из списка."""
    while True:
        print(prompt)
        for i, o in enumerate(options, 1):
            print(f"  {i}) {o}")
        ans = input("> ").strip()
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1]
        # прямой ввод имени тоже принимаем
        if ans and ans in options:
            return ans
        print("Некорректный выбор, попробуйте ещё раз.\n")


def detect_db_path() -> str:
    """Пытаемся получить путь к БД из config.py, потом mini_150825.db, иначе спрашиваем."""
    # 1) config.py рядом со скриптом
    here = os.path.abspath(os.path.dirname(__file__))
    sys.path.insert(0, here)
    db_path = None

    try:
        import config  # type: ignore
        # приоритет: DB_PATH, потом Config.SQLALCHEMY_DATABASE_URI (sqlite:///...)
        path_from_config = getattr(config, "DB_PATH", None)
        if path_from_config and os.path.isfile(path_from_config):
            db_path = path_from_config
        else:
            uri = getattr(getattr(config, "Config", object), "SQLALCHEMY_DATABASE_URI", "")
            if isinstance(uri, str) and uri.startswith("sqlite:///"):
                # sqlite:///C:/... или относительный путь
                possible = uri.replace("sqlite:///", "", 1)
                # нормализуем
                if not os.path.isabs(possible):
                    possible = os.path.abspath(os.path.join(here, possible))
                if os.path.isfile(possible):
                    db_path = possible
    except Exception:
        pass  # config.py может отсутствовать — это ок

    # 2) файл в текущей папке со скриптом
    if not db_path:
        candidate = os.path.abspath(os.path.join(here, DEFAULT_DB_FILENAME))
        if os.path.isfile(candidate):
            db_path = candidate

    # 3) спросить у пользователя
    while not db_path:
        p = input(f"Укажи полный путь к SQLite БД (пример C:/path/{DEFAULT_DB_FILENAME}):\n> ").strip().strip('"')
        if p and os.path.isfile(p):
            db_path = os.path.abspath(p)
        else:
            print("Файл не найден. Попробуй ещё раз.\n")

    return db_path


def engine_from_path(db_path: str) -> Engine:
    # Преобразуем в SQLite URI
    uri = "sqlite:///" + db_path.replace("\\", "/")
    log.info(f"Подключаемся к БД: {uri}")
    return create_engine(uri, future=True)


def reflect_table(engine: Engine, name: str) -> Table:
    meta = MetaData()
    return Table(name, meta, autoload_with=engine)


def ensure_users_table(engine: Engine) -> tuple[Table, str, str, str]:
    """Ищем таблицу пользователей и проверяем, что там есть нужные колонки.
    Если дефолты не подошли — даём выбрать из доступных таблиц/колонок.
    """
    insp = inspect(engine)
    tables = insp.get_table_names()
    if not tables:
        raise RuntimeError("В БД нет таблиц. Проверь, точно ли это нужный файл?")

    # Сначала пытаемся 'user', затем 'users'
    candidates = [DEFAULT_TABLE_PRIMARY, DEFAULT_TABLE_FALLBACK]
    last_err = None
    for t in candidates:
        if t in tables:
            try:
                tbl = reflect_table(engine, t)
                cols = set(tbl.c.keys())
                required = {DEFAULT_ID_COL, DEFAULT_LOGIN_COL, DEFAULT_PASS_COL}
                if required.issubset(cols):
                    log.info(f"Используем таблицу '{t}' (схема подходит).")
                    return tbl, DEFAULT_ID_COL, DEFAULT_LOGIN_COL, DEFAULT_PASS_COL
                else:
                    last_err = RuntimeError(
                        f"Таблица '{t}' найдена, но нет колонок {required}. Есть: {sorted(cols)}"
                    )
            except Exception as e:
                last_err = e

    # Если не вышло — интерактивный выбор
    print("\nАвтовыбор таблицы не удался.")
    print("Список таблиц в БД:")
    for t in tables:
        print(" -", t)
    table_name = pick("\nВыбери таблицу с пользователями:", tables)
    tbl = reflect_table(engine, table_name)
    all_cols = list(tbl.c.keys())
    print("\nКолонки таблицы:", ", ".join(all_cols), "\n")

    id_col = pick("Выбери колонку c ID пользователя:", all_cols)
    login_col = pick("Выбери колонку с логином/именем пользователя:", all_cols)
    pass_col = pick("Выбери колонку с хешем пароля:", all_cols)
    return tbl, id_col, login_col, pass_col


def ask_user_selector(login_col: str, id_col: str) -> tuple[str, str]:
    """Возвращает ('id'|'login', значение)."""
    mode = pick("\nКак искать пользователя?", ["По ID", f"По {login_col}"])
    if mode == "По ID":
        while True:
            s = input("Введи ID пользователя:\n> ").strip()
            if s.isdigit():
                return "id", s
            print("Нужно число. Попробуй ещё раз.")
    else:
        while True:
            s = input(f"Введи {login_col} пользователя:\n> ").strip()
            if s:
                return "login", s


def ask_new_password() -> str:
    import os, sys
    from getpass import getpass

    def ask_visible() -> str:
        while True:
            p1 = input("Новый пароль (видимый ввод): ").strip()
            p2 = input("Повторите пароль: ").strip()
            if p1 != p2:
                print("Пароли не совпадают. Ещё раз.\n", flush=True)
                continue
            if len(p1) < 4:
                print("Слишком короткий пароль (минимум 4 символа). Ещё раз.\n", flush=True)
                continue
            return p1

    def ask_hidden() -> str:
        while True:
            p1 = getpass("Новый пароль: ")
            p2 = getpass("Повторите пароль: ")
            if p1 != p2:
                print("Пароли не совпадают. Ещё раз.\n", flush=True)
                continue
            if len(p1) < 4:
                print("Слишком короткий пароль (минимум 4 символа). Ещё раз.\n", flush=True)
                continue
            return p1

    # Определяемся с режимом: в PyCharm и при отсутствии TTY — используем видимый ввод
    is_pycharm = os.environ.get("PYCHARM_HOSTED") == "1"
    has_tty = sys.stdin.isatty() and sys.stdout.isatty()

    if is_pycharm or not has_tty:
        print("(PyCharm/нет TTY) Перехожу на ВИДИМЫЙ ввод пароля.\n", flush=True)
        return ask_visible()

    # Иначе спросим, как удобнее
    print("Как вводить пароль?", flush=True)
    print("  1) Скрыто (рекомендуется)", flush=True)
    print("  2) Видимо", flush=True)
    choice = input("> ").strip()
    if choice == "2":
        return ask_visible()
    return ask_hidden()


def main():
    try:
        db_path = detect_db_path()
        log.info(f"Файл БД: {db_path}")
        engine = engine_from_path(db_path)

        users, id_col, login_col, pass_col = ensure_users_table(engine)

        mode, selector = ask_user_selector(login_col, id_col)

        # Находим пользователя
        with engine.connect() as conn:
            if mode == "id":
                sel = select(users).where(users.c[id_col] == int(selector))
                row = conn.execute(sel).mappings().first()
            else:
                sel = select(users).where(users.c[login_col] == selector)
                rows = conn.execute(sel).mappings().all()
                if len(rows) > 1:
                    print(f"Найдено несколько записей с {login_col}='{selector}'. Уточни поиск по ID.")
                    sys.exit(2)
                row = rows[0] if rows else None

        if not row:
            print("Пользователь не найден.")
            sys.exit(1)

        user_id = row[id_col]
        user_login = row.get(login_col, "<нет_логина>")
        print(f"\nНайден пользователь: {id_col}={user_id}, {login_col}='{user_login}'")

        # Показать текущий хэш до изменения
        with engine.connect() as conn:
            before_row = conn.execute(
                select(users.c[pass_col]).where(users.c[id_col] == user_id)
            ).fetchone()
            before_hash = before_row[0] if before_row else None
        print(f"Текущий password_hash (до): {before_hash}")

        # Новый пароль
        new_password = ask_new_password()
        new_hash = generate_password_hash(new_password)
        print(f"Новый password_hash (будет записан): {new_hash}")

        # Обновляем с явной транзакцией и синхронизацией
        with engine.begin() as conn:
            upd = (
                update(users)
                .where(users.c[id_col] == user_id)
                .values({pass_col: new_hash})
            )
            res = conn.execute(upd)
            affected = res.rowcount or 0
            print(f"UPDATE затронул строк: {affected}")

        # Считываем снова и показываем результат
        with engine.connect() as conn:
            after_row = conn.execute(
                select(users.c[pass_col]).where(users.c[id_col] == user_id)
            ).fetchone()
            after_hash = after_row[0] if after_row else None
        print(f"Текущий password_hash (после): {after_hash}")

        if after_hash == new_hash and affected == 1:
            print("✅ Пароль успешно обновлён и проверен повторным чтением.")
            sys.exit(0)
        else:
            print("⚠️ Что-то не так: хэш не совпадает после UPDATE.")
            sys.exit(3)

        # Новый пароль
        new_password = ask_new_password()
        new_hash = generate_password_hash(new_password)

        # Обновляем
        with engine.begin() as conn:
            upd = (
                update(users)
                .where(users.c[id_col] == user_id)
                .values({pass_col: new_hash})
            )
            res = conn.execute(upd)
            if res.rowcount == 1:
                print("Пароль успешно обновлён.")
                sys.exit(0)
            else:
                print(f"Неожиданно затронуто строк: {res.rowcount or 0}.")
                sys.exit(3)

    except SQLAlchemyError as e:
        log.exception(f"Ошибка SQLAlchemy: {e}")
        sys.exit(10)
    except KeyboardInterrupt:
        print("\nОтмена.")
        sys.exit(130)
    except Exception as e:
        log.exception(f"Ошибка: {e}")
        sys.exit(11)


if __name__ == "__main__":
    main()
