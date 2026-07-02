# ایمیج ربات چت ناشناس ملوگپ‌طور
FROM python:3.12-slim

WORKDIR /app

# نصب وابستگی‌های سیستمی مورد نیاز برای asyncpg و geoalchemy2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
