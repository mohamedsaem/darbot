# Drive Sheet Archive Bot

بوت تيليجرام جديد بالكامل يقرأ من Google Sheet مبني من فهرسة Google Drive.

## ماذا يفعل؟
- يتصفح الفولدرات كأزرار
- يقف عند آخر فولدر فقط ويعطيك رابط الفولدر
- لا ينزل إلى الملفات داخل آخر فولدر
- فيه بحث برقم أمر الصرف
- البحث يطلب الشركة أولًا ثم رقم الأمر

## الأعمدة المطلوبة في الشيت
لازم الشيت يحتوي الأعمدة التالية بالضبط:
- Name
- Folder_ID
- Parent_ID
- Level
- Path
- Top_Section
- Company
- Is_Leaf
- Link

## إعدادات البيئة
انسخ `.env.example` إلى `.env` ثم ضع القيم:
- `TELEGRAM_BOT_TOKEN`
- `SHEET_ID`
- `SHEET_GID` (لو الشيت في أول tab اتركها 0 أو ضع gid الصحيح)
- `BOT_TITLE`

## مهم جدًا
لازم الشيت يكون:
- `Anyone with the link` = `Viewer`

## تشغيل محليًا
```bash
pip install -r requirements.txt
python bot.py
```

## تشغيل على Railway
ضع المتغيرات التالية في Variables:
- `TELEGRAM_BOT_TOKEN`
- `SHEET_ID`
- `SHEET_GID`
- `BOT_TITLE`

Start command:
```bash
python bot.py
```

## ملاحظات
- لو أضفت فولدرات جديدة على Drive، شغّل Apps Script مرة أخرى لتحديث الشيت.
- لو عدّلت الشيت، اضغط داخل البوت على `تحديث البيانات`.
