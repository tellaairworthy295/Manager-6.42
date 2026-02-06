import time
from datetime import datetime, timedelta, date
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Date,
)
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import IntegrityError
import urllib

# =========================
# Configuration
# =========================

REMOTE_DB_URL = f"mysql+pymysql://root:{urllib.parse.quote_plus('112358@gh')}@8.153.91.114:35300/dify_data"
LOCAL_DB_URL = f"mysql+pymysql://lmh:{urllib.parse.quote_plus('lmh@123456')}@10.29.88.63:3306/selfdev_test"

POLL_INTERVAL_SECONDS = 1 * 60   # 1 minutes
LOOKBACK_MINUTES = 20             # fetch last 20 minutes

Base = declarative_base()

# =========================
# Model
# =========================

class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = {
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_unicode_ci",
    }

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(500), unique=True, nullable=False, index=True)
    source = Column(String(100))
    title = Column(Text)
    content = Column(Text)
    title_zh = Column(Text)
    content_zh = Column(Text)
    xml_content = Column(Text)
    content_fully_loaded = Column(Boolean, default=False)
    publish_at = Column(DateTime, nullable=False)
    scraped_date = Column(Date, default=date.today, index=True)
    scraped_at = Column(DateTime, default=datetime.now)

# =========================
# Database setup
# =========================

remote_engine = create_engine(
    REMOTE_DB_URL,
    pool_pre_ping=True,
)

local_engine = create_engine(
    LOCAL_DB_URL,
    pool_pre_ping=True,
)

RemoteSession = sessionmaker(bind=remote_engine)
LocalSession = sessionmaker(bind=local_engine)

# =========================
# Sync logic (Upsert All)
# =========================

def sync_once():
    now = datetime.now()
    since_time = now - timedelta(minutes=LOOKBACK_MINUTES)

    remote_session = RemoteSession()
    local_session = LocalSession()

    try:
        # 1. Fetch recent remote records
        remote_rows = (
            remote_session.query(NewsArticle)
            .filter(NewsArticle.scraped_at >= since_time)
            .all()
        )
        
        if not remote_rows:
            print(f"[{datetime.now()}] No new remote rows.")
            return

        upserted = 0
        for row in remote_rows:
            # Try fetch by URL
            local_article = local_session.query(NewsArticle).filter_by(url=row.url).one_or_none()
            if local_article is not None:
                # Update all fields
                local_article.source = row.source
                local_article.title = row.title
                local_article.content = row.content
                local_article.title_zh = row.title_zh
                local_article.content_zh = row.content_zh
                local_article.xml_content = row.xml_content
                local_article.content_fully_loaded = row.content_fully_loaded
                local_article.publish_at = row.publish_at
                local_article.scraped_date = row.scraped_date
                local_article.scraped_at = row.scraped_at
            else:
                # Insert new
                new_article = NewsArticle(
                    url=row.url,
                    source=row.source,
                    title=row.title,
                    content=row.content,
                    title_zh=row.title_zh,
                    content_zh=row.content_zh,
                    xml_content=row.xml_content,
                    content_fully_loaded=row.content_fully_loaded,
                    publish_at=row.publish_at,
                    scraped_date=row.scraped_date,
                    scraped_at=row.scraped_at,
                )
                local_session.add(new_article)
            upserted += 1

        if upserted:
            local_session.commit()
            print(f"[{datetime.now()}] Upserted {upserted} rows (inserted or updated).")
        else:
            print(f"[{datetime.now()}] No rows to upsert.")

    except IntegrityError as e:
        local_session.rollback()
        print("Integrity error occurred during upsert, rolled back.", e)

    except Exception as e:
        local_session.rollback()
        print("Sync failed:", e)

    finally:
        remote_session.close()
        local_session.close()

# =========================
# Polling loop
# =========================

def run_forever():
    print("Starting MySQL news table sync...")
    while True:
        sync_once()
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    run_forever()
