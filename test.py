from sqlalchemy import create_engine, text

# Create a connection engine to the SQLite database
engine = create_engine("sqlite:///K:/dichfoto_video/app.db")

def check_video_table_columns():
    """
    التحقق من أسماء الأعمدة في جدول 'videos'.

    تتصل هذه الدالة بقاعدة البيانات وتستخرج أسماء الأعمدة باستخدام
    تعليمة PRAGMA الخاصة بـ SQLite.
    """
    with engine.begin() as connection:
        columns_info = connection.execute(text("PRAGMA table_info(videos)")).fetchall()
        column_names = [column[1] for column in columns_info]
        print("COLUMNS:", column_names)

def display_video_records():
    """
    عرض السجلات من جدول 'videos' للتحقق من وجود بيانات في عمود 'vimeo_hash'.

    تقوم هذه الدالة بعرض الحقول التالية من كل سجل: id, provider, video_id, vimeo_hash.
    """
    with engine.begin() as connection:
        records = connection.execute(
            text("SELECT id, provider, video_id, vimeo_hash FROM videos ORDER BY id DESC")
        ).fetchall()
        for record in records:
            print(record)

# تنفيذ الدوال للتحقق والعرض
check_video_table_columns()
display_video_records()
