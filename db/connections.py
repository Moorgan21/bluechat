# Copyright (C) 2026 Dariush Lashani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""اتصال به Postgres + PostGIS با SQLAlchemy async.

نیاز به `pip install sqlalchemy[asyncio] asyncpg geoalchemy2`.

پسوند PostGIS باید روی دیتابیس فعال باشه (`CREATE EXTENSION IF NOT
EXISTS postgis;`) - نیاز به دسترسی superuser داره، ولی init_db() خودش
تلاش می‌کنه فعالش کنه اگه یوزر دیتابیس دسترسی کافی داشته باشه.

اتصال با env var به اسم DATABASE_URL تنظیم می‌شه. با ایمیج رسمی
postgres:16 پوستگیس نداری؛ باید postgis/postgis:16-3.4 رو بگیری:
    docker run -d --name bluechat-pg -e POSTGRES_USER=bluechat \
      -e POSTGRES_PASSWORD=bluechat -e POSTGRES_DB=bluechat \
      -p 5432:5432 postgis/postgis:16-3.4

اگه سرور Postgres رو با پروژه‌های دیگه شریک می‌شی، یه دیتابیس/اسکیمای
جدا (مثلاً `bluechat`) بساز که جداولش قاطی نشه.
"""

import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://bluechat:bluechat@localhost:5432/bluechat"
)
READ_DATABASE_URL = os.environ.get("READ_DATABASE_URL", "").strip() or DATABASE_URL

_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "40"))

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=_POOL_SIZE, max_overflow=_MAX_OVERFLOW)
async_session = async_sessionmaker(engine, expire_on_commit=False)

# اگه READ_DATABASE_URL ست شده (یعنی read replica داریم)، یه engine جدا
# برای query های خواندنی می‌سازیم که بار primary کم بشه، وگرنه همون
# engine اصلی رو به اشتراک می‌ذاریم.
_read_engine = (
    create_async_engine(READ_DATABASE_URL, echo=False, pool_size=_POOL_SIZE, max_overflow=_MAX_OVERFLOW)
    if READ_DATABASE_URL != DATABASE_URL
    else engine
)
read_session = async_sessionmaker(_read_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from . import models  # noqa: F401  # باید قبل از create_all ایمپورت بشه تا مدل‌ها رجیستر بشن

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)
        # ایندکس مکانی GiST برای جستجوی سریع «نزدیک‌ترین‌ها»
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_users_location ON users USING GIST (location)")
        )
