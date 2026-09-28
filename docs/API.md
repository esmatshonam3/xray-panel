# مرجع API

آدرس پایه: `https://<your-panel>/api/v1`
مستندات تعاملی (فقط در محیط غیرتولیدی): `/api/docs`

## احراز هویت

| روش | توضیح |
|---|---|
| `Authorization: Bearer <jwt>` | توکن نشست پنل (پیش‌فرض ۱۲ ساعت) |
| `X-API-Key: xp_…` | کلید بلندمدت برای اتوماسیون، ساخته‌شده با `POST /auth/api-key` |

نقش‌ها: `user` < `support` < `admin` < `owner`.

---

## احراز هویت — `/auth`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| POST | `/login` | عمومی | نام کاربری + رمز (و `totp_code`). جفت توکن برمی‌گرداند. محدودیت: `LOGIN_MAX_ATTEMPTS` در `LOGIN_WINDOW_SECONDS`، سپس خطای ۴۲۹ با هدر `Retry-After`. |
| POST | `/register` | عمومی | ثبت‌نام خودکار؛ `referral_code` را می‌پذیرد |
| POST | `/refresh` | عمومی | تبدیل توکن refresh به جفت تازه |
| POST | `/logout` | هر نقش | ثبت در لاگ حسابرسی |
| GET | `/me` | هر نقش | پروفایل جاری |
| POST | `/change-password` | هر نقش | نیازمند رمز فعلی؛ رمز فقط‌حرفی یا فقط‌عددی رد می‌شود |
| POST | `/totp/setup` | هر نقش | برگرداندن secret و URI مربوط به `otpauth://` |
| POST | `/totp/enable` | هر نقش | `?code=123456` |
| POST | `/totp/disable` | هر نقش | — |
| POST | `/api-key` | هر نقش | چرخش کلید API؛ متن خام فقط یک‌بار نمایش داده می‌شود |

```bash
curl -sX POST "$PANEL/api/v1/auth/login" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"…"}' | jq -r .access_token
```

---

## کاربران — `/users`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `` | support+ | فهرست صفحه‌بندی‌شده: `?page=&size=&q=&status_filter=&role=` |
| POST | `` | admin+ | ساخت کاربر (فقط `owner` می‌تواند `admin`/`owner` بسازد) |
| GET | `/{id}` | support+ | کاربر + جمع تعداد سرویس و مصرف |
| PATCH | `/{id}` | admin+ | ایمیل، نقش، وضعیت، یادداشت، موجودی، رمز |
| POST | `/{id}/suspend` | admin+ | غیرفعال‌سازی کاربر **و** قطع همه‌ی کانفیگ‌های فعال |
| POST | `/{id}/activate` | admin+ | فعال‌سازی مجدد |
| POST | `/{id}/unlock` | admin+ | باز کردن قفل ورود |
| DELETE | `/{id}` | admin+ | حذف نرم کانفیگ‌ها و حذف ردیف کاربر |

---

## پلن‌ها — `/plans`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `` | هر نقش | `?include_inactive=true` برای کارکنان |
| GET | `/public` | عمومی | فهرست فروشگاه (فقط فعال و عمومی) |
| POST | `` | admin+ | ساخت |
| PATCH | `/{id}` | admin+ | ویرایش |
| DELETE | `/{id}` | admin+ | حذف کامل، یا غیرفعال‌سازی اگر سرویسی به آن وصل باشد |

---

## سرویس‌ها (کانفیگ‌ها) — `/services`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `` | هر نقش | کاربر فقط سرویس‌های خودش را می‌بیند؛ فیلترها: `status_filter`, `node_id`, `user_id`, `q` |
| POST | `` | admin+ | ساخت؛ `inbound_id` اختیاری است (خودش سالم‌ترین نود منطبق را انتخاب می‌کند) |
| GET | `/{id}` | مالک/ادمین | جزئیات کامل همراه لینک‌ها و آدرس QR |
| PATCH | `/{id}` | admin+ | برچسب، وضعیت، سهمیه، انقضا، تمدید خودکار |
| POST | `/{id}/renew` | admin+ | `{days, reset_traffic, add_traffic_gb}` |
| POST | `/{id}/reset-traffic` | admin+ | صفر کردن شمارنده‌ها و فعال‌سازی مجدد |
| POST | `/{id}/sync` | admin+ | ارسال مجدد کاربر به نود |
| POST | `/{id}/pull-usage` | admin+ | خواندن فوری آمار همان نود |
| POST | `/bulk` | admin+ | `{service_ids, action: enable\|disable\|delete\|reset_traffic\|sync}` |
| GET | `/{id}/link` | مالک/ادمین | URI خام به‌صورت `text/plain` |
| GET | `/{id}/qr.png` | مالک/ادمین | تصویر QR |
| DELETE | `/{id}` | admin+ | حذف از نود و دیتابیس |

---

## نودها و اینباندها — `/nodes`, `/inbounds`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `/nodes` | support+ | فهرست با وضعیت زنده و تعداد سرویس |
| POST | `/nodes` | admin+ | ساخت (بلافاصله `/health` را صدا می‌زند) |
| PATCH | `/nodes/{id}` | admin+ | ویرایش، شامل چرخش توکن ایجنت |
| GET | `/nodes/{id}/health` | support+ | probe زنده |
| POST | `/nodes/{id}/sync` | admin+ | ارسال اینباندها و کاربران (اعمال وضعیت مطلوب) |
| POST | `/nodes/{id}/restart-xray` | admin+ | ری‌لود هسته |
| POST | `/nodes/{id}/rotate-token` | admin+ | توکن `nd_…` جدید (بعدش ایجنت را به‌روز کنید) |
| DELETE | `/nodes/{id}` | admin+ | تا وقتی سرویس فعال دارد رد می‌شود |
| GET | `/inbounds` | support+ | `?node_id=` |
| POST | `/inbounds` | admin+ | ساخت (کلیدهای Reality پذیرفته می‌شود) |
| PATCH | `/inbounds/{id}` | admin+ | ویرایش + همگام‌سازی خودکار |
| GET | `/inbounds/{id}/validate` | support+ | بررسی ایستا: کلیدهای Reality، تعارض پورت، سلامت ترنسپورت |
| POST | `/inbounds/{id}/sync` | admin+ | اعمال وضعیت مطلوب همان اینباند |
| GET | `/inbounds/{id}/clients` | support+ | فهرست زنده‌ی کلاینت‌ها از نود |

---

## اشتراک عمومی — `/sub` و `/subscription`

بدون احراز هویت، با کلید ۳۲ کاراکتری `sub_token`. محدودیت نرخ اعمال می‌شود.

هر دو پیشوند دقیقاً یک هندلر را سرو می‌کنند:

* `/sub/<token>` — **مسیر کوتاهی که در کانفیگ مشتری می‌نشیند** (خروجی `Service.subscription_url`)
* `/api/v1/subscription/<token>` — نام‌فضای API (سازگاری با نسخه‌های قبلی)

| متد | مسیر | توضیح |
|---|---|---|
| GET | `/{token}` | بدنه‌ی اشتراک. `?format=base64` (پیش‌فرض)، `plain`، `clash`. هدرهای `Subscription-Userinfo`، `Profile-Title` و `Profile-Update-Interval` ست می‌شوند. |
| GET | `/{token}/clash.yaml` | پروفایل Clash.Meta / mihomo |
| GET | `/{token}/info` | وضعیت JSON (مصرف ربات و رابط کاربری) |
| GET | `/{token}/qr.png` | QR لینک اتصال (`?size=4..20`) |

برای سرویس منقضی، حجم‌تمام یا معلق **۴۰۳** و برای توکن نامعتبر یا حذف‌شده **۴۰۴**
برمی‌گردد. مسیرهای ناشناس زیر `/sub/` و `/api/` همیشه **۴۰۴ JSON** می‌دهند و
هرگز HTML پنل را برنمی‌گردانند.

---

## پرداخت‌ها — `/payments`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `` | هر نقش | کارکنان همه را می‌بینند؛ کاربر فقط خودش |
| GET | `/pending` | support+ | صف بررسی |
| GET | `/mine` | هر نقش | تاریخچه‌ی خود |
| POST | `` | هر نقش | ساخت سفارش (`purchase`، `renewal`؛ روش `manual`، `crypto`، `telegram_stars`، `gateway`، `balance`) |
| POST | `/{id}/approve` | admin+ | تسویه → ساخت/تمدید سرویس → پرداخت پورسانت زیرمجموعه |
| POST | `/{id}/reject` | admin+ | `{reason}` |
| POST | `/{id}/refund` | admin+ | بازگشت مبلغ به کیف پول |

---

## گزارش‌ها — `/reports`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `/dashboard` | هر نقش | داده‌ی کارت‌های KPI (برای کاربر عادی محدود به خودش) |
| GET | `/traffic` | هر نقش | سری روزانه، `?days=&user_id=&service_id=` |
| GET | `/top-consumers` | هر نقش | پرمصرف‌ترین سرویس‌ها |
| GET | `/services.csv` | support+ | خروجی کامل CSV |
| GET | `/revenue` | support+ | مبلغ و تعداد پرداخت روزانه |
| GET | `/summary` | support+ | تجمیع بر اساس وضعیت / پروتکل / نود |

---

## مانیتورینگ

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `/health/live` | عمومی | liveness (healthcheck پلتفرم) |
| GET | `/health/ready` | عمومی | دسترسی به دیتابیس |
| GET | `/health` | support+ | بررسی عمیق: دیتابیس، زمان‌بند، نودها، هشدارها، تلگرام |
| GET | `/metrics` | عمومی | قالب Prometheus |
| GET | `/status` | عمومی | بنر غیرحساس صفحه‌ی ورود |
| GET | `/alerts` | support+ | `?active_only=&limit=` |
| POST | `/alerts` | admin+ | ثبت هشدار دستی |
| POST | `/alerts/{id}/ack` | admin+ | تأیید و بستن |
| GET | `/alerts/node/{id}/metrics` | support+ | تاریخچه‌ی متریک نود |

---

## تنظیمات، حسابرسی، پشتیبان

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `/settings` | support+ | بازنویسی‌های زمان‌اجرا |
| PUT | `/settings/{key}` | admin+ | فقط کلیدهای مجاز قابل نوشتن‌اند |
| GET | `/settings/public` | عمومی | برندینگ و اطلاعات پرداخت |
| GET | `/logs/audit` | support+ | `?action=&actor_id=&entity_type=&page=&size=` |
| GET | `/backups` | support+ | فهرست + مصرف دیسک |
| POST | `/backups` | admin+ | ساخت فوری |
| POST | `/backups/{filename}/restore` | admin+ | روی Postgres مدیریت‌شده در تولید مسدود است |
| POST | `/backups/prune` | admin+ | اعمال سیاست نگه‌داری |
| GET | `/system/info` | admin+ | گزارش تنظیمات مؤثر |

---

## تلگرام — `/telegram`

| متد | مسیر | دسترسی | توضیح |
|---|---|---|---|
| GET | `/config` | admin+ | تنظیمات مؤثر ربات؛ توکن و کلید مخفی فقط **ماسک‌شده** برمی‌گردند |
| PUT | `/config` | admin+ | ذخیره‌ی توکن / نام کاربری / کلید وبهوک / شناسه ادمین‌ها / کلیدهای فعال‌سازی |
| DELETE | `/config` | admin+ | حذف همه‌ی بازنویسی‌های پنل و بازگشت به متغیرهای محیطی |
| POST | `/generate-secret` | admin+ | تولید کلید مخفی تصادفی وبهوک |
| POST | `/validate` | admin+ | بررسی توکن با `getMe`. `?token=` برای تست پیش از ذخیره |
| POST | `/test` | admin+ | ارسال پیام آزمایشی به همه‌ی شناسه‌های ادمین |
| POST | `/setup` | admin+ | ثبت وبهوک در تلگرام |
| POST | `/unset-webhook` | admin+ | حذف وبهوک |
| GET | `/status` | admin+ | هویت ربات، اطلاعات وبهوک، تعداد حساب‌های متصل |
| POST | `/webhook/{secret}` | تلگرام | دریافت آپدیت (اعتبارسنجی با کلید مخفی ذخیره‌شده) |

**ترتیب اولویت مقادیر:** دیتابیس پنل → متغیر محیطی → مقدار پیش‌فرض.
هر تغییری که از `PUT /config` ذخیره شود بلافاصله در همان پروسه اعمال می‌شود
(بدون ری‌استارت). پاسخ شامل `token_source` و `admin_ids_source` است تا UI
نشان دهد مقدار مؤثر از کجا آمده.

```bash
# ذخیره‌ی توکن و شناسه ادمین
curl -X PUT "$PANEL/api/v1/telegram/config" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"bot_token":"123456:ABC...","admin_ids":[123456789],"enabled":true}'

# بررسی توکن پیش از ذخیره
curl -X POST "$PANEL/api/v1/telegram/validate?token=123456:ABC..." \
  -H "Authorization: Bearer $TOKEN"
```

**اعتبارسنجی:** توکن باید شکل `123456789:AA...` داشته باشد و شناسه‌ها عدد
مثبت باشند؛ در غیر این صورت **۴۲۲** برمی‌گردد. رشته‌ی خالی به معنی «مقدار
فعلی را دست نزن» است و `clear_token: true` توکن ذخیره‌شده را پاک می‌کند.

---

## خطاها

| کد | معنا |
|---|---|
| 400 | نقض قاعده‌ی کسب‌وکار (مثلاً موجودی ناکافی) |
| 401 | توکن نامعتبر/منقضی، رمز اشتباه، کد 2FA اشتباه |
| 403 | نقش ناکافی، یا اشتراک منقضی/معلق |
| 404 | منبع یا توکن اشتراک ناشناس |
| 409 | تعارض (نام کاربری تکراری، نبود اینباند مناسب، پلن در استفاده) |
| 422 | خطای اعتبارسنجی (`{"detail": "Validation error", "errors": [...]}`) |
| 423 | حساب موقتاً قفل شده پس از تلاش‌های ناموفق |
| 429 | محدودیت نرخ، هدر `Retry-After` ست است |
| 502 | ایجنت نود در دسترس نیست یا درخواست را رد کرده |
