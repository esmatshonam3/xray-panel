"""Bot translations (Persian / English).

Kept separate from the web UI dictionary: the bot needs different wording and a
much smaller surface. `tr(lang, key, **vars)` is the single accessor.
"""
from __future__ import annotations

from typing import Any, Dict

FA: Dict[str, str] = {
    # ---------------------------------------------------------------- generic
    "lang.pick": "زبان خود را انتخاب کنید 👇",
    "lang.set": "زبان به فارسی تغییر کرد ✅",
    "btn.back": "⬅️ بازگشت",
    "btn.home": "🏠 منوی اصلی",
    "btn.cancel": "✖️ انصراف",
    "common.yes": "✅ بله",
    "common.no": "❌ خیر",
    "common.on": "روشن",
    "common.off": "خاموش",
    "common.unlimited": "نامحدود",
    "common.days": "روز",
    "common.daysLeft": "{n} روز مانده",
    "common.expired": "منقضی شده",
    "common.unknown": "نامشخص",
    "common.notFound": "موردی یافت نشد.",
    "common.denied": "⛔️ دسترسی ندارید.",
    "common.notUsable": "⛔️ این کانفیگ قابل استفاده نیست ({status}). لطفاً تمدید کنید.",
    "common.tooFast": "⏳ کمی آرام‌تر! چند لحظه بعد دوباره تلاش کنید.",
    "common.error": "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.",
    "common.copied": "برای کپی، روی متن زیر بزنید:",

    # ---------------------------------------------------------------- start
    "start.welcome": (
        "👋 <b>خوش آمدید به {app}</b>\n\n"
        "از این ربات می‌توانید سرویس خود را بخرید، تمدید کنید و کانفیگ بگیرید.\n"
        "همه‌چیز خودکار است — بدون نیاز به پشتیبانی."
    ),
    "start.newAccount": "حساب شما ساخته شد ✅",
    "start.linked": "حساب پنل شما متصل است ✅",
    "help": (
        "🤖 <b>راهنمای ربات</b>\n\n"
        "/start — منوی اصلی\n"
        "/account — وضعیت حساب\n"
        "/configs — کانفیگ‌های من\n"
        "/plans — خرید سرویس\n"
        "/wallet — کیف پول\n"
        "/support — پشتیبانی\n"
        "/lang — تغییر زبان\n"
        "/admin — پنل مدیریت (فقط ادمین)\n\n"
        "💡 برای دریافت کانفیگ: «کانفیگ‌های من» → انتخاب کانفیگ → «دریافت لینک»"
    ),

    # ---------------------------------------------------------------- menu
    "menu.title": "🏠 <b>منوی اصلی</b>\nیک گزینه را انتخاب کنید:",
    "menu.account": "👤 حساب من",
    "menu.configs": "📦 کانفیگ‌های من",
    "menu.plans": "🛒 خرید سرویس",
    "menu.renew": "🔄 تمدید سرویس",
    "menu.usage": "📊 مصرف من",
    "menu.wallet": "💳 کیف پول",
    "menu.referral": "🎁 زیرمجموعه",
    "menu.settings": "⚙️ تنظیمات",
    "menu.support": "🆘 پشتیبانی",
    "menu.admin": "🛠 پنل مدیریت",

    # ---------------------------------------------------------------- account
    "account.title": "👤 <b>حساب کاربری</b>",
    "account.username": "نام کاربری",
    "account.id": "شناسه",
    "account.status": "وضعیت",
    "account.balance": "موجودی کیف پول",
    "account.configs": "تعداد کانفیگ",
    "account.active": "فعال",
    "account.disabled": "غیرفعال",
    "account.usage": "مصرف کل",
    "account.joined": "تاریخ عضویت",

    # ---------------------------------------------------------------- configs
    "configs.title": "📦 <b>کانفیگ‌های من</b>\nیکی را انتخاب کنید:",
    "configs.empty": "📦 هنوز کانفیگی ندارید.\nاز «خرید سرویس» شروع کنید.",
    "configs.detail": "📦 <b>{label}</b>  <code>#{id}</code>",
    "configs.protocol": "پروتکل",
    "configs.node": "نود",
    "configs.status": "وضعیت",
    "configs.usage": "مصرف",
    "configs.expires": "انقضا",
    "configs.getLink": "🔗 دریافت لینک",
    "configs.sub": "📥 لینک اشتراک",
    "configs.qr": "🖼 تصویر QR",
    "configs.renew": "🔄 تمدید",
    "configs.rename": "✏️ تغییر نام",
    "configs.delete": "🗑 حذف",
    "configs.link": "🔗 <b>لینک اتصال</b>\n<code>{link}</code>",
    "configs.subTitle": "📥 <b>لینک اشتراک</b>\nبرای v2rayNG، Clash، Streisand و ...\n<code>{url}</code>\n\nنسخه Clash:\n<code>{clash}</code>",
    "configs.deleted": "🗑 کانفیگ حذف شد.",
    "configs.deleteConfirm": "این کانفیگ حذف شود؟ این عمل بازگشت‌پذیر نیست.",
    "configs.renamePrompt": "✏️ نام جدید را ارسال کنید:",
    "configs.renamed": "✅ نام کانفیگ تغییر کرد.",
    "configs.renamedTo": "✅ نام کانفیگ به «{name}» تغییر کرد.",

    # ---------------------------------------------------------------- plans
    "plans.title": "🛒 <b>پلن‌های موجود</b>\nیک پلن را انتخاب کنید:",
    "plans.empty": "در حال حاضر پلنی برای فروش موجود نیست.",
    "plans.detail": "🛒 <b>{name}</b>\n\nمدت: <b>{days} روز</b>\nحجم: <b>{traffic}</b>\nقیمت: <b>{price} {currency}</b>",
    "plans.chooseMethod": "روش پرداخت را انتخاب کنید:",
    "plans.payBalance": "💳 پرداخت از کیف پول",
    "plans.payCard": "🏦 کارت به کارت",
    "plans.payCrypto": "🪙 کریپتو",
    "plans.balanceLow": "⛔️ موجودی کیف پول کافی نیست ({balance} {currency}).\nابتدا کیف پول را شارژ کنید یا کارت به کارت پرداخت کنید.",
    "plans.payNow": "🧾 <b>سفارش ایجاد شد</b>",
    "plans.orderRef": "کد پیگیری",
    "plans.amount": "مبلغ",
    "plans.receiptHint": "پس از پرداخت، <b>عکس رسید</b> را همین‌جا ارسال کنید.",
    "plans.balancePaid": "🎉 پرداخت از کیف پول انجام شد!\nسرویس شما فعال است.\nاز «کانفیگ‌های من» لینک را بگیرید.",
    "plans.activated": "🎉 سفارش تأیید و سرویس فعال شد!",
    "plans.renewTitle": "🔄 <b>کدام سرویس را تمدید می‌کنید؟</b>",
    "plans.renewPlan": "🔄 تمدید <b>{label}</b>\nپلن تمدید را انتخاب کنید:",
    "plans.noService": "سرویسی برای تمدید وجود ندارد.",

    # ---------------------------------------------------------------- usage
    "usage.title": "📊 <b>گزارش مصرف</b>",
    "usage.total": "جمع کل",
    "usage.empty": "مصرفی ثبت نشده است.",

    # ---------------------------------------------------------------- wallet
    "wallet.title": "💳 <b>کیف پول</b>",
    "wallet.balance": "موجودی",
    "wallet.earnings": "درآمد زیرمجموعه",
    "wallet.recent": "آخرین تراکنش‌ها",
    "wallet.none": "تراکنشی ثبت نشده است.",
    "wallet.topup": "➕ شارژ کیف پول",
    "wallet.topupPrompt": "مبلغ موردنظر را به دلار ارسال کنید (مثلاً 10):",
    "wallet.topupCreated": "🧾 درخواست شارژ <code>{ref}</code> به مبلغ <b>{amount} {currency}</b> ساخته شد.\n\n{instructions}\n\nپس از پرداخت، عکس رسید را ارسال کنید.",

    # ---------------------------------------------------------------- referral
    "referral.title": "🎁 <b>زیرمجموعه</b>",
    "referral.code": "کد شما",
    "referral.link": "لینک دعوت",
    "referral.invited": "تعداد دعوت‌شده",
    "referral.earned": "درآمد کسب‌شده",
    "referral.hint": "از هر خرید زیرمجموعه، {percent}% به کیف پول شما اضافه می‌شود.",

    # ---------------------------------------------------------------- support
    "support.title": "🆘 <b>پشتیبانی</b>",
    "support.faq": (
        "<b>سؤال‌های پرتکرار</b>\n"
        "• کانفیگ را چطور وصل کنم؟ لینک اشتراک را در v2rayNG یا Clash وارد کنید.\n"
        "• سرویس قطع شده؟ ممکن است حجم تمام شده یا منقضی شده باشد؛ «کانفیگ‌های من» را ببینید.\n"
        "• پرداخت تأیید نشده؟ پس از ارسال رسید، بررسی معمولاً چند دقیقه طول می‌کشد."
    ),
    "support.contact": "ارتباط با پشتیبانی",

    # ---------------------------------------------------------------- settings
    "settings.title": "⚙️ <b>تنظیمات</b>",
    "settings.language": "🌐 زبان",
    "settings.notifications": "🔔 اعلان‌ها",
    "settings.notifExpiry": "یادآوری انقضا",
    "settings.notifQuota": "اعلان اتمام حجم",
    "settings.notifNews": "اخبار و تخفیف‌ها",
    "settings.saved": "✅ تنظیمات ذخیره شد.",
    "settings.langSaved": "✅ زبان تغییر کرد.",
    "settings.myId": "🆔 شناسه من",

    # ---------------------------------------------------------------- identity
    "identity.title": "🆔 <b>شناسه تلگرام شما</b>",
    "identity.chatId": "شناسه عددی",
    "identity.username": "نام کاربری",
    "identity.role": "نقش",
    "identity.admin": "ادمین",
    "identity.customer": "کاربر",
    "identity.hint": (
        "این عدد را در پنل، بخش «ربات تلگرام» → «شناسه ادمین‌ها» وارد کنید "
        "تا دسترسی کنسول مدیریتی فعال شود."
    ),

    # ---------------------------------------------------------------- admin
    "admin.title": "🛠 <b>پنل مدیریت</b>",
    "admin.stats": "📊 آمار کلی",
    "admin.users": "👥 کاربران",
    "admin.payments": "💳 پرداخت‌های در انتظار",
    "admin.nodes": "🗄 نودها",
    "admin.alerts": "🚨 هشدارها",
    "admin.plans": "🏷 پلن‌ها",
    "admin.report": "📈 گزارش",
    "admin.broadcast": "📢 اعلان همگانی",
    "admin.find": "🔎 جستجوی کاربر",
    "admin.backup": "💾 پشتیبان‌گیری",
    "admin.denied": "⛔️ این بخش فقط برای ادمین است.",
    "admin.statsBody": (
        "📊 <b>آمار کلی</b>\n\n"
        "کاربران: <b>{users}</b>\n"
        "کانفیگ فعال: <b>{active}</b> از <b>{total}</b>\n"
        "نود آنلاین: <b>{nodesOnline}/{nodesTotal}</b>\n"
        "پرداخت در انتظار: <b>{pending}</b>\n"
        "هشدار فعال: <b>{alerts}</b>\n"
        "درآمد امروز: <b>{revenue} {currency}</b>"
    ),
    "admin.usersBody": "👥 <b>آخرین کاربران</b>",
    "admin.usersHint": "برای مشاهده و مدیریت هر کاربر، روی آن بزنید.",
    "admin.userCard": (
        "👤 <code>{id}</code> <b>{username}</b>\n"
        "وضعیت: {status} · نقش: {role}\n"
        "موجودی: <b>{balance} {currency}</b>\n"
        "کانفیگ: <b>{configs}</b> · مصرف: <b>{usage}</b>"
    ),
    "admin.userRenewed": "✅ سرویس‌های کاربر تمدید شد.",
    "admin.userToggled": "✅ وضعیت کاربر تغییر کرد.",
    "admin.balancePrompt": "مبلغی که به موجودی اضافه شود را ارسال کنید (مثلاً 10):",
    "admin.balanceAdded": "✅ موجودی کاربر افزایش یافت.",
    "admin.configCreated": "✅ کانفیگ ساخته و به کاربر اطلاع داده شد.",
    "admin.noPayments": "💳 پرداخت در انتظاری وجود ندارد.",
    "admin.paymentsBody": "💳 <b>{n} پرداخت در انتظار</b> (۵ مورد ارسال شد)",
    "admin.paymentApproved": "✅ تأیید شد و سرویس فعال گردید.",
    "admin.paymentRejected": "❌ سفارش رد شد.",
    "admin.nodesBody": "🗄 <b>نودها</b>",
    "admin.noNodes": "نودی ثبت نشده است.",
    "admin.nodeSynced": "✅ همگام‌سازی شد ({n} اینباند).",
    "admin.nodeHealth": "❤️ <b>سلامت {name}</b>\n\nوضعیت: {status}\nXray: {xray}\nCPU: <b>{cpu}%</b> · RAM: <b>{ram}%</b> · Disk: <b>{disk}%</b>\nکاربر آنلاین: <b>{users}</b>",
    "admin.alertsBody": "🚨 <b>هشدارهای فعال</b>",
    "admin.noAlerts": "هشدار فعالی وجود ندارد.",
    "admin.alertAcked": "✅ هشدار بسته شد.",
    "admin.plansBody": "🏷 <b>پلن‌ها</b>",
    "admin.planToggled": "✅ وضعیت پلن تغییر کرد.",
    "admin.reportBody": "📈 <b>گزارش سرویس‌ها</b>",
    "admin.broadcastPrompt": "📢 متن پیام همگانی را ارسال کنید (HTML مجاز است).",
    "admin.broadcastTarget": "مخاطبان را انتخاب کنید:",
    "admin.broadcastAll": "👥 همه کاربران",
    "admin.broadcastActive": "🟢 فقط دارندگان سرویس فعال",
    "admin.broadcastSent": "✅ پیام برای {n} کاربر ارسال شد.",
    "admin.findPrompt": "🔎 نام کاربری، ایمیل یا شناسه عددی را ارسال کنید.",
    "admin.findEmpty": "کاربری یافت نشد.",
    "admin.backupDone": "💾 پشتیبان ساخته شد:\n<code>{file}</code> ({size})",
    "admin.backupFailed": "❌ پشتیبان‌گیری ناموفق بود: {error}",

    # ---------------------------------------------------------------- notify
    "notify.quota": "⛔️ حجم سرویس <b>{label}</b> به پایان رسید.\nبرای ادامه، سرویس را تمدید کنید.",
    "notify.expiring": "⏳ سرویس <b>{label}</b> تا <b>{days}</b> روز دیگر منقضی می‌شود.\nبرای جلوگیری از قطعی، تمدید کنید.",
    "notify.expired": "🔴 سرویس <b>{label}</b> منقضی شد.\nهمین حالا می‌توانید آن را فعال کنید.",
    "notify.receiptOk": "🧾 رسید شما برای سفارش <code>{ref}</code> ثبت شد.\nپس از تأیید ادمین، سرویس خودکار فعال می‌شود.",
    "notify.noOrder": "سفارش در انتظاری پیدا نشد. ابتدا از منو سرویس بخرید.",
    "notify.rejected": "❌ سفارش <code>{ref}</code> رد شد.\nبرای پیگیری با پشتیبانی تماس بگیرید.",
}

EN: Dict[str, str] = {
    "lang.pick": "Choose your language 👇",
    "lang.set": "Language switched to English ✅",
    "btn.back": "⬅️ Back",
    "btn.home": "🏠 Main menu",
    "btn.cancel": "✖️ Cancel",
    "common.yes": "✅ Yes",
    "common.no": "❌ No",
    "common.on": "On",
    "common.off": "Off",
    "common.unlimited": "Unlimited",
    "common.days": "days",
    "common.daysLeft": "{n} days left",
    "common.expired": "Expired",
    "common.unknown": "Unknown",
    "common.notFound": "Not found.",
    "common.denied": "⛔️ Access denied.",
    "common.notUsable": "⛔️ This config is not usable ({status}). Please renew.",
    "common.tooFast": "⏳ Slow down! Try again in a moment.",
    "common.error": "❌ Something went wrong. Please retry.",
    "common.copied": "Tap the text below to copy:",

    "start.welcome": (
        "👋 <b>Welcome to {app}</b>\n\n"
        "Buy, renew and manage your configs right here in Telegram.\n"
        "Everything is automated — no support ticket needed."
    ),
    "start.newAccount": "Your account is ready ✅",
    "start.linked": "Your panel account is linked ✅",
    "help": (
        "🤖 <b>Bot help</b>\n\n"
        "/start — main menu\n"
        "/account — account status\n"
        "/configs — my configs\n"
        "/plans — buy a plan\n"
        "/wallet — wallet\n"
        "/support — support\n"
        "/lang — change language\n"
        "/admin — admin console (staff only)\n\n"
        "💡 To get a config: My configs → pick one → Get link"
    ),

    "menu.title": "🏠 <b>Main menu</b>\nPick an option:",
    "menu.account": "👤 My account",
    "menu.configs": "📦 My configs",
    "menu.plans": "🛒 Buy a plan",
    "menu.renew": "🔄 Renew",
    "menu.usage": "📊 My usage",
    "menu.wallet": "💳 Wallet",
    "menu.referral": "🎁 Referral",
    "menu.settings": "⚙️ Settings",
    "menu.support": "🆘 Support",
    "menu.admin": "🛠 Admin console",

    "account.title": "👤 <b>My account</b>",
    "account.username": "Username",
    "account.id": "ID",
    "account.status": "Status",
    "account.balance": "Wallet balance",
    "account.configs": "Configs",
    "account.active": "Active",
    "account.disabled": "Disabled",
    "account.usage": "Total usage",
    "account.joined": "Joined",

    "configs.title": "📦 <b>My configs</b>\nPick one:",
    "configs.empty": "📦 You have no configs yet.\nStart with “Buy a plan”.",
    "configs.detail": "📦 <b>{label}</b>  <code>#{id}</code>",
    "configs.protocol": "Protocol",
    "configs.node": "Node",
    "configs.status": "Status",
    "configs.usage": "Usage",
    "configs.expires": "Expires",
    "configs.getLink": "🔗 Get link",
    "configs.sub": "📥 Subscription",
    "configs.qr": "🖼 QR image",
    "configs.renew": "🔄 Renew",
    "configs.rename": "✏️ Rename",
    "configs.delete": "🗑 Delete",
    "configs.link": "🔗 <b>Connection URI</b>\n<code>{link}</code>",
    "configs.subTitle": "📥 <b>Subscription URL</b>\nFor v2rayNG, Clash, Streisand…\n<code>{url}</code>\n\nClash profile:\n<code>{clash}</code>",
    "configs.deleted": "🗑 Config deleted.",
    "configs.deleteConfirm": "Delete this config? This cannot be undone.",
    "configs.renamePrompt": "✏️ Send the new name:",
    "configs.renamed": "✅ Config renamed.",
    "configs.renamedTo": "✅ Config renamed to “{name}”.",

    "plans.title": "🛒 <b>Available plans</b>\nPick one:",
    "plans.empty": "No plan is on sale right now.",
    "plans.detail": "🛒 <b>{name}</b>\n\nDuration: <b>{days} days</b>\nTraffic: <b>{traffic}</b>\nPrice: <b>{price} {currency}</b>",
    "plans.chooseMethod": "Choose a payment method:",
    "plans.payBalance": "💳 Pay from wallet",
    "plans.payCard": "🏦 Bank transfer",
    "plans.payCrypto": "🪙 Crypto",
    "plans.balanceLow": "⛔️ Wallet balance is too low ({balance} {currency}).\nTop up your wallet or pay by bank transfer.",
    "plans.payNow": "🧾 <b>Order created</b>",
    "plans.orderRef": "Reference",
    "plans.amount": "Amount",
    "plans.receiptHint": "After paying, send the <b>receipt photo</b> here.",
    "plans.balancePaid": "🎉 Paid from your wallet!\nYour service is active.\nGet the link from “My configs”.",
    "plans.activated": "🎉 Payment approved and service activated!",
    "plans.renewTitle": "🔄 <b>Which service do you want to renew?</b>",
    "plans.renewPlan": "🔄 Renewing <b>{label}</b>\nPick a renewal plan:",
    "plans.noService": "No service available to renew.",

    "usage.title": "📊 <b>Usage report</b>",
    "usage.total": "Total",
    "usage.empty": "No usage recorded yet.",

    "wallet.title": "💳 <b>Wallet</b>",
    "wallet.balance": "Balance",
    "wallet.earnings": "Referral earnings",
    "wallet.recent": "Recent transactions",
    "wallet.none": "No transactions yet.",
    "wallet.topup": "➕ Top up",
    "wallet.topupPrompt": "Send the amount in USD (e.g. 10):",
    "wallet.topupCreated": "🧾 Top-up request <code>{ref}</code> for <b>{amount} {currency}</b> created.\n\n{instructions}\n\nSend the receipt photo once paid.",

    "referral.title": "🎁 <b>Referral</b>",
    "referral.code": "Your code",
    "referral.link": "Invite link",
    "referral.invited": "Invited",
    "referral.earned": "Earned",
    "referral.hint": "You earn {percent}% of every purchase your referrals make.",

    "support.title": "🆘 <b>Support</b>",
    "support.faq": (
        "<b>FAQ</b>\n"
        "• How do I connect? Import the subscription URL into v2rayNG or Clash.\n"
        "• Service stopped? You may be out of quota or expired — check “My configs”.\n"
        "• Payment not approved? Review usually takes a few minutes after the receipt."
    ),
    "support.contact": "Contact support",

    "settings.title": "⚙️ <b>Settings</b>",
    "settings.language": "🌐 Language",
    "settings.notifications": "🔔 Notifications",
    "settings.notifExpiry": "Expiry reminders",
    "settings.notifQuota": "Quota warnings",
    "settings.notifNews": "News & offers",
    "settings.saved": "✅ Settings saved.",
    "settings.langSaved": "✅ Language changed.",
    "settings.myId": "🆔 My ID",

    "identity.title": "🆔 <b>Your Telegram ID</b>",
    "identity.chatId": "Numeric ID",
    "identity.username": "Username",
    "identity.role": "Role",
    "identity.admin": "Admin",
    "identity.customer": "Customer",
    "identity.hint": (
        "Paste this number into the panel under “Telegram bot” → “Admin IDs” "
        "to unlock the admin console."
    ),

    "admin.title": "🛠 <b>Admin console</b>",
    "admin.stats": "📊 Stats",
    "admin.users": "👥 Users",
    "admin.payments": "💳 Pending payments",
    "admin.nodes": "🗄 Nodes",
    "admin.alerts": "🚨 Alerts",
    "admin.plans": "🏷 Plans",
    "admin.report": "📈 Report",
    "admin.broadcast": "📢 Broadcast",
    "admin.find": "🔎 Find user",
    "admin.backup": "💾 Backup",
    "admin.denied": "⛔️ Admins only.",
    "admin.statsBody": (
        "📊 <b>Overview</b>\n\n"
        "Users: <b>{users}</b>\n"
        "Active configs: <b>{active}</b> of <b>{total}</b>\n"
        "Nodes online: <b>{nodesOnline}/{nodesTotal}</b>\n"
        "Pending payments: <b>{pending}</b>\n"
        "Active alerts: <b>{alerts}</b>\n"
        "Revenue today: <b>{revenue} {currency}</b>"
    ),
    "admin.usersBody": "👥 <b>Recent users</b>",
    "admin.usersHint": "Tap a user to manage them.",
    "admin.userCard": (
        "👤 <code>{id}</code> <b>{username}</b>\n"
        "Status: {status} · Role: {role}\n"
        "Balance: <b>{balance} {currency}</b>\n"
        "Configs: <b>{configs}</b> · Usage: <b>{usage}</b>"
    ),
    "admin.userRenewed": "✅ User services renewed.",
    "admin.userToggled": "✅ User status changed.",
    "admin.balancePrompt": "Send the amount to add to the balance (e.g. 10):",
    "admin.balanceAdded": "✅ Balance updated.",
    "admin.configCreated": "✅ Config created and the user was notified.",
    "admin.noPayments": "💳 No pending payments.",
    "admin.paymentsBody": "💳 <b>{n} pending payment(s)</b> (5 shown)",
    "admin.paymentApproved": "✅ Approved and service activated.",
    "admin.paymentRejected": "❌ Order rejected.",
    "admin.nodesBody": "🗄 <b>Nodes</b>",
    "admin.noNodes": "No nodes registered.",
    "admin.nodeSynced": "✅ Synced ({n} inbounds).",
    "admin.nodeHealth": "❤️ <b>{name} health</b>\n\nStatus: {status}\nXray: {xray}\nCPU: <b>{cpu}%</b> · RAM: <b>{ram}%</b> · Disk: <b>{disk}%</b>\nOnline users: <b>{users}</b>",
    "admin.alertsBody": "🚨 <b>Active alerts</b>",
    "admin.noAlerts": "No active alerts.",
    "admin.alertAcked": "✅ Alert acknowledged.",
    "admin.plansBody": "🏷 <b>Plans</b>",
    "admin.planToggled": "✅ Plan toggled.",
    "admin.reportBody": "📈 <b>Service report</b>",
    "admin.broadcastPrompt": "📢 Send the broadcast text (HTML allowed).",
    "admin.broadcastTarget": "Choose the audience:",
    "admin.broadcastAll": "👥 All users",
    "admin.broadcastActive": "🟢 Only users with an active service",
    "admin.broadcastSent": "✅ Sent to {n} user(s).",
    "admin.findPrompt": "🔎 Send a username, email or numeric ID.",
    "admin.findEmpty": "No user matched.",
    "admin.backupDone": "💾 Backup created:\n<code>{file}</code> ({size})",
    "admin.backupFailed": "❌ Backup failed: {error}",

    "notify.quota": "⛔️ Traffic on <b>{label}</b> is exhausted.\nRenew to keep using it.",
    "notify.expiring": "⏳ <b>{label}</b> expires in <b>{days}</b> day(s).\nRenew to avoid interruption.",
    "notify.expired": "🔴 <b>{label}</b> has expired.\nYou can reactivate it right now.",
    "notify.receiptOk": "🧾 Receipt for <code>{ref}</code> received.\nThe service activates automatically once an admin approves it.",
    "notify.noOrder": "No pending order found. Buy a service from the menu first.",
    "notify.rejected": "❌ Order <code>{ref}</code> was rejected.\nPlease contact support.",
}

DICTS = {"fa": FA, "en": EN}
RTL_LANGS = {"fa", "ar", "he", "ur"}


def tr(lang: str, key: str, **vars: Any) -> str:
    """Translate `key` for `lang`, falling back to English then to the key."""
    table = DICTS.get(lang) or EN
    text = table.get(key)
    if text is None:
        text = EN.get(key, key)
    if vars:
        for name, value in vars.items():
            text = text.replace("{" + name + "}", str(value))
    return text


def normalize(lang: str | None) -> str:
    """Map a Telegram `language_code` to one we actually support."""
    if not lang:
        return "fa"
    code = lang.lower()[:2]
    return code if code in DICTS else ("fa" if code in RTL_LANGS else "en")


def status_label(lang: str, status: str) -> str:
    mapping = {
        "active": {"fa": "فعال", "en": "Active"},
        "expired": {"fa": "منقضی", "en": "Expired"},
        "limited": {"fa": "اتمام حجم", "en": "Quota used"},
        "disabled": {"fa": "غیرفعال", "en": "Disabled"},
        "pending": {"fa": "در انتظار", "en": "Pending"},
        "deleted": {"fa": "حذف‌شده", "en": "Deleted"},
    }
    return mapping.get(status, {}).get(lang, status)


def status_icon(status: str) -> str:
    return {"active": "🟢", "expired": "🔴", "limited": "🟠", "disabled": "⚪️"}.get(status, "⚪️")
