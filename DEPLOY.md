# راه‌اندازی و دیپلوی CFO جیبی

این فایل کارهایی است که **یک‌بار برای همیشه** باید انجام دهی. بعد از آن، هر `push`
به‌صورت خودکار روی سرور دیپلوی می‌شود (GitHub Actions → Docker روی VPS).

مدل کار: ربات با **polling** اجرا می‌شود، پس به دامنه/HTTPS نیازی نیست. کلیدهای
حساس فقط در فایل `.env` روی سرور می‌مانند و هیچ‌وقت وارد گیت‌هاب نمی‌شوند.

---

## الف) کلیدها را جمع کن
1. **توکن ربات:** در تلگرام به [@BotFather](https://t.me/BotFather) برو و یک ربات بساز → یک توکن می‌گیری.
2. **کلید OpenRouter:** از [openrouter.ai/keys](https://openrouter.ai/keys).
3. **آی‌دی عددی تلگرامت:** از [@userinfobot](https://t.me/userinfobot) (یک عدد مثل `123456789`).

## ب) سرور را آماده کن (VPS خارجی، اوبونتو ۲۲.۰۴+)
```bash
ssh user@SERVER_IP

# نصب داکر و پلاگین compose
curl -fsSL https://get.docker.com | sh
docker compose version          # باید نسخه را نشان دهد

# دایرکتوری پروژه
mkdir -p ~/pocket-cfo
```

حالا فایل `~/pocket-cfo/.env` را بساز — فقط همین سه کلید محرمانه:
```bash
cat > ~/pocket-cfo/.env <<'EOF'
TELEGRAM_BOT_TOKEN=توکن_رباتت
OPENROUTER_API_KEY=کلید_OpenRouter
ALLOWED_USER_IDS=آی‌دی_عددی_خودت
EOF
```
> بقیه‌ی تنظیمات (مدل‌ها، تایم‌اوت، واحد، ساعت یادآوری) داخل کد در
> `bot/config.py` هستند؛ برای عوض‌کردنشان کد را ویرایش و push کن، نه سرور را.
> `DB_PATH` هم لازم نیست؛ داکر خودش روی volume پایدار ست می‌کند.

## ج) کلید دیپلوی بساز تا Actions بتواند SSH بزند
روی لپ‌تاپ خودت (نه سرور):
```bash
ssh-keygen -t ed25519 -f deploy_key -N ""
```
۱. محتوای **کلید عمومی** (`deploy_key.pub`) را به `~/.ssh/authorized_keys` روی سرور اضافه کن:
```bash
ssh-copy-id -i deploy_key.pub user@SERVER_IP
# یا دستی محتوای deploy_key.pub را در انتهای ~/.ssh/authorized_keys سرور بچسبان
```
۲. در گیت‌هاب، در مسیر **Settings → Secrets and variables → Actions** این Secretها را بساز:

| نام Secret | مقدار |
|---|---|
| `SSH_HOST` | آی‌پی سرور |
| `SSH_USER` | یوزر SSH (مثلاً `root`) |
| `SSH_KEY` | کل محتوای فایل **خصوصی** `deploy_key` |
| `SSH_PORT` | اختیاری، اگر پورت SSH غیر از ۲۲ است |

> کلیدهای اپ (توکن تلگرام و OpenRouter) را اینجا **نگذار**؛ آن‌ها در `.env` روی سرورند.

### میان‌برِ iOS — بدون هیچ کارِ اضافه
`INGEST_PUBLIC_URL` را دیپلوی **خودش** از روی `SSH_HOST` می‌سازد
(`http://<host>:8791`) و در `~/pocket-cfo/.deploy.env` روی سرور می‌نویسد؛ نه به `.env`
دست می‌زند نه از تو چیزی می‌خواهد. کاربر در تلگرام `/shortcut` می‌زند و لینکِ کاملِ
شخصی‌اش را می‌گیرد، پس لازم نیست کسی آی‌پیِ سرور را بداند.

اگر روزی دامنه/Cloudflare جلوی سرور آمد: در **Settings → Secrets and variables →
Actions → Variables** یک Variable به نامِ `INGEST_PUBLIC_URL` بساز (مثلاً
`https://cfo.example.com`) — همان بر پیش‌فرض ارجح است. Variable است نه Secret، چون
آدرس محرمانه نیست و اینطوری در لاگِ دیپلوی هم دیده می‌شود.

دیپلوی اگر `ufw` روی سرور فعال باشد پورت ۸۷۹۱ را هم باز می‌کند. اگر ارائه‌دهنده‌ی
سرور فایروالِ ابریِ جدا دارد (مثل Hetzner Cloud Firewall)، آن یکی را باید در پنلِ
خودش باز کنی — از داخلِ سرور قابلِ تنظیم نیست.

## د) اولین دیپلوی
- یک `push` بزن، یا در تب **Actions** گیت‌هاب، ورک‌فلو **Deploy** را دستی Run کن.
- وقتی سبز شد، در تلگرام `/start` را بزن و یک ویس تست بفرست. ✅

---

## بعد از این مرحله (کارهای جاری)
- هر `push` به برنچ توسعه → دیپلوی خودکار.
- **دیدن لاگ‌ها:** تب Actions → ورک‌فلو **Logs** → Run workflow.
- **تست‌ها:** ورک‌فلو **CI** روی هر push اجرا می‌شود.

## دستورهای مفید روی سرور
```bash
cd ~/pocket-cfo
docker compose ps            # وضعیت کانتینر
docker compose logs -f bot   # لاگ زنده
docker compose restart bot   # ری‌استارت
docker compose down          # خاموش کردن
```

## عیب‌یابی سریع
- **ربات جواب نمی‌دهد:** `docker compose logs bot` را ببین؛ معمولاً توکن یا `.env` اشتباه است.
- **پیام «سرویس‌های هوش مصنوعی پاسخگو نیستند»:** کلید OpenRouter یا دسترسی شبکه‌ی سرور را چک کن.
- **یادآوری شبانه نمی‌آید:** `ALLOWED_USER_IDS` باید آی‌دی عددی واقعی‌ات باشد و حداقل یک تراکنش ناقص وجود داشته باشد.
