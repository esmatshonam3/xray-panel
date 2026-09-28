# ایجنت نود Xray

این سرویس، **دیتاپلین** سیستم است: پروسه‌ی `xray-core` را اجرا می‌کند و یک API
کوچک و امضاشده در اختیار پنل می‌گذارد. پنل تنها منبع حقیقت است؛ این سرویس
وضعیت مطلوب را می‌گیرد، کانفیگ را می‌سازد، اعتبارسنجی می‌کند و اعمال می‌کند.

## اجرا

```bash
docker build -t xray-node .

docker run -d --name xray-node \
  --restart unless-stopped \
  --network host \
  -e NODE_TOKEN="$(openssl rand -hex 32)" \
  -e NODE_NAME="eu-1" \
  -e PUBLIC_HOST="node1.example.com" \
  -e AGENT_PORT=8081 \
  -v /etc/xray:/etc/xray \
  -v /var/log/xray:/var/log/xray \
  xray-node
```

`--network host` باعث می‌شود Xray هر پورتی را بدون `-p` اضافه bind کند.
اگر از شبکه‌ی bridge استفاده می‌کنید، هر پورت اینباند را با `-p` منتشر کنید.

## متغیرهای محیطی

| نام | پیش‌فرض | توضیح |
|---|---|---|
| `NODE_TOKEN` | — | **الزامی.** توکن مشترک با پنل. با کاما می‌توان چند توکن داد (برای چرخش بدون قطعی). |
| `NODE_NAME` | `node-1` | نام نمایشی |
| `PUBLIC_HOST` | — | هاست عمومی این نود (برای رندر SNI پیش‌فرض) |
| `AGENT_PORT` | `8081` | پورت API ایجنت |
| `XRAY_API_PORT` | `10085` | پورت داخلی HandlerService/StatsService |
| `XRAY_BIN` | `/usr/local/bin/xray` | مسیر باینری Xray |
| `XRAY_CONFIG_PATH` | `/etc/xray/config.json` | کانفیگ تولیدشده |
| `XRAY_STATE_PATH` | `/etc/xray/state.json` | وضعیت مطلوب (اینباندها + کاربران) |
| `XRAY_LOG_LEVEL` | `warning` | سطح لاگ هسته |
| `SIGNATURE_TTL_SECONDS` | `300` | پنجره‌ی مجاز اختلاف زمانی امضا |
| `ALLOW_INSECURE_SIGNATURE` | `false` | فقط برای دیباگ محلی؛ در تولید روشن نکنید |

## API

عمومی (بدون احراز هویت، برای probe):

| متد | مسیر | توضیح |
|---|---|---|
| GET | `/health` | وضعیت پروسه و Xray |
| GET | `/ping` | پاسخ سریع |

خصوصی (Bearer + امضای HMAC):

| متد | مسیر | توضیح |
|---|---|---|
| GET | `/health/deep` | + CPU/RAM/دیسک/شبکه؛ هسته‌ی مرده را خودکار بالا می‌آورد |
| GET | `/system` | متریک‌های میزبان |
| GET | `/stats?reset=` | شمارنده‌ی آپلینک/داونلینک هر کاربر |
| POST | `/users` | افزودن یک کاربر |
| DELETE | `/users/{tag}/{email}` | حذف یک کاربر |
| POST | `/users/apply` | جایگزینی کاربران یک اینباند |
| POST | `/inbounds/apply` | جایگزینی کل اینباندها + رندر و اعتبارسنجی کانفیگ |
| GET | `/config/{tag}` | کانفیگ مؤثر یک اینباند (اشکال‌زدایی) |
| POST | `/restart` | ری‌لود هسته |
| POST | `/reality-keys` | تولید جفت‌کلید X25519 |

### امضای درخواست

```
Authorization: Bearer <NODE_TOKEN>
X-Node-Timestamp: <ثانیه‌ی یونیکس>
X-Node-Signature: HMAC_SHA256(NODE_TOKEN, timestamp || raw_request_body)
```

اختلاف زمانی بیش از `SIGNATURE_TTL_SECONDS` رد می‌شود تا درخواست شنود‌شده
قابل بازپخش نباشد.

## راهبرد اعمال تغییر

1. تغییرات کاربران ابتدا با API زمان‌اجرای Xray (`xray api adu` / `rmu`) اعمال
   می‌شوند — بدون قطعی.
2. اگر هر عملیات hot شکست بخورد (معمولاً تفاوت امضای CLI در نسخه‌های مختلف
   Xray)، ایجنت به مسیر پشتیبان برمی‌گردد: نوشتن `config.json`، اعتبارسنجی با
   `xray run -test` و ری‌استارت پروسه.
3. تغییر در مجموعه‌ی اینباندها همیشه reload کامل می‌کند.

مسیر پشتیبان همیشه درست است، فقط کوتاه‌مدت قطعی دارد؛ بنابراین یک اعمال خراب
هرگز نود را در وضعیت ناسازگار رها نمی‌کند.

## افزودن پروتکل جدید

1. یک عضو به enum `Protocol` در `panel/app/db/models.py` اضافه کنید.
2. سازنده‌ی لینک را در `panel/app/services/xray_links.py` بنویسید.
3. رندرر کلاینت را در `agent/xray_manager.py::_render_client` و
   `_render_inbound` اضافه کنید.
4. شکل حساب را در `agent/xray_api.py::_user_payload` تعریف کنید.

## نکات عملیاتی

* **فایروال:** فقط پورت‌های اینباند را باز کنید. پورت `8081` (API ایجنت) باید
  یا در شبکه‌ی خصوصی بماند یا پشت TLS و محدودکننده‌ی نرخ باشد.
* **گواهی TLS اینباند:** برای اینباندهای TLS، فایل‌های `cert.pem` و `key.pem`
  را در `/etc/xray/` بگذارید و در اینباند (فیلدهای `extra.cert_file` و
  `extra.key_file`) به آن‌ها اشاره کنید.
* **بلاک‌لیست:** کانفیگ پیش‌فرض ترافیک bittorrent را به `blackhole` می‌فرستد.
  اگر سیاست استفاده‌ی منصفانه‌ی شما متفاوت است،
  `agent/xray_manager.py::render_config` را ویرایش کنید.
* **بازیابی:** اگر نود از دست رفت، VPS جدید با همان `NODE_TOKEN` بالا بیاورید،
  در پنل آدرس نود را عوض کنید و `POST /nodes/{id}/sync` بزنید — همه‌ی
  اینباندها و کاربران دوباره فرستاده می‌شوند.
* **مسئولیت استفاده:** استفاده از این ابزار تابع قوانین محلی، قوانین
  ارائه‌دهنده‌ی میزبانی و شرایط استفاده‌ی سرویس‌های بالادستی است. از آن برای
  سوءاستفاده، اسپم یا حمله به اشخاص ثالث استفاده نکنید.
