# `.railway/` — Infrastructure as Code (اختیاری)

این پوشه شامل پیکربندی **جدید** Railway است. برای یک دیپلوی معمولی
(وصل کردن مخزن به Railway از داشبورد) **لازم نیست** و هیچ تأثیری روی آن ندارد.

## چرا اینجاست

فایل‌های `railway.toml` که در `panel/` و `node-agent/` هستند از طرف Railway
**منسوخ** شده‌اند و طبق مستندات رسمی تا **۲۰۲۶-۱۲-۰۱** کار می‌کنند. فایل
`railway.ts` جانشین آن‌هاست.

## چطور استفاده می‌شود

```bash
railway link                 # اتصال به پروژه
railway config pull          # اگر منابع از قبل ساخته شده‌اند، اول import کن
railway config plan          # فقط نمایش تغییرات — چیزی اعمال نمی‌شود
railway config apply         # بعد از تأیید plan
```

> ⚠️ همیشه اول `plan` را ببینید. اگر پروژه را از قبل در داشبورد ساخته‌اید و
> `pull` نزنید، `apply` ممکن است منابع تکراری بسازد.

## چرا این فایل مهم است

مخزن یک **monorepo** است و اپ در ریشه نیست. اگر Root Directory تنظیم نشود،
Railway با Railpack از ریشه بیلد می‌کند و با این خطا شکست می‌خورد:

```
⚠ Script start.sh not found
✖ Railpack could not determine how to build the app.
```

در `railway.ts` مقدار `rootDirectory` صریحاً تعریف شده:

```typescript
source: github("esmatshonam3/xray-panel", { branch: "main", rootDirectory: "panel" })
```

پس با `apply`، این خطا ساختاراً غیرممکن می‌شود.

## اگر مخزن را fork کرده‌اید

مقدار `REPO` در ابتدای فایل را عوض کنید.

## محدودیت‌ها

* **TCP Proxy** برای سرویس نود در این فایل قابل بیان نیست — باید دستی در
  `Settings → Networking` ساخته شود.
* **Volume** برای `/app/data` (پشتیبان‌گیری) هم فعلاً از داشبورد ساخته می‌شود.
* این فایل را با `railway config plan` تست نکرده‌ام چون به یک پروژه‌ی واقعی
  Railway نیاز دارد؛ قبل از `apply` خروجی `plan` را با دقت ببینید.
