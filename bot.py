import logging
import os
from io import StringIO
from typing import List

import pandas as pd
import requests
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("archive_bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SHEET_ID = os.getenv("SHEET_ID", "")
SHEET_GID = os.getenv("SHEET_GID", "0")
BOT_TITLE = os.getenv("BOT_TITLE", "بوت أرشيف الشركات")

STATE_IDLE = "idle"
STATE_AWAIT_ORDER_NUMBER = "await_order_number"


def check_env() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not SHEET_ID:
        missing.append("SHEET_ID")
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def sheet_csv_url() -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    # تنظيف أسماء الأعمدة
    df.columns = [str(c).strip() for c in df.columns]

    # تنظيف القيم
    for col in df.columns:
        df[col] = df[col].astype(str).fillna("").map(lambda x: str(x).strip())

    return df


def load_index() -> pd.DataFrame:
    resp = requests.get(sheet_csv_url(), timeout=30)
    resp.raise_for_status()

    # مهم جدًا للعربي + BOM
    text = resp.content.decode("utf-8-sig")

    df = pd.read_csv(StringIO(text), dtype=str).fillna("")
    df = normalize_df(df)

    required = [
        "Name",
        "Folder_ID",
        "Parent_ID",
        "Level",
        "Path",
        "Top_Section",
        "Company",
        "Is_Leaf",
        "Link",
    ]
    for col in required:
        if col not in df.columns:
            raise RuntimeError(f"Missing required column in sheet: {col}")

    return df


def get_root_row(df: pd.DataFrame) -> pd.Series:
    roots = df[df["Level"] == "0"]
    if roots.empty:
        raise RuntimeError("No root folder row found in sheet. Expected one row with Level = 0.")
    return roots.iloc[0]


def children_of(df: pd.DataFrame, folder_id: str) -> pd.DataFrame:
    rows = df[df["Parent_ID"] == folder_id].copy()
    return rows.sort_values(by=["Top_Section", "Company", "Name"], kind="stable")


def top_sections(df: pd.DataFrame) -> pd.DataFrame:
    root = get_root_row(df)
    return children_of(df, str(root["Folder_ID"]))


def unique_companies(df: pd.DataFrame) -> List[str]:
    rows = df[
        (df["Top_Section"] == "أوامر الصرف")
        & (df["Company"] != "")
        & (df["Company"] != "official_docs")
    ].copy()

    companies = sorted({str(c).strip() for c in rows["Company"].tolist() if str(c).strip()})
    return companies


def search_order_folders(df: pd.DataFrame, company: str, order_number: str) -> pd.DataFrame:
    order_number = str(order_number).strip()

    rows = df[
        (df["Top_Section"] == "أوامر الصرف")
        & (df["Company"] == company)
        & (df["Name"].astype(str).str.strip() == order_number)
        & (df["Is_Leaf"].astype(str).str.upper() == "YES")
    ].copy()

    return rows.sort_values(by=["Path", "Name"], kind="stable")


def find_folder_by_id(df: pd.DataFrame, folder_id: str) -> pd.DataFrame:
    return df[df["Folder_ID"] == folder_id].copy()


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📂 تصفح الأرشيف", callback_data="browse_root")],
            [InlineKeyboardButton("🔎 بحث برقم أمر الصرف", callback_data="search_order")],
            [InlineKeyboardButton("♻️ تحديث البيانات", callback_data="refresh")],
        ]
    )


def folder_buttons(rows: pd.DataFrame, back_callback: str = "home") -> InlineKeyboardMarkup:
    buttons = []
    for _, row in rows.iterrows():
        name = str(row["Name"]).strip()[:35]
        buttons.append([InlineKeyboardButton(name, callback_data=f"open:{row['Folder_ID']}")])

    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data=back_callback)])
    return InlineKeyboardMarkup(buttons)


def companies_keyboard(companies: List[str]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(c[:35], callback_data=f"company:{c}")] for c in companies]
    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def result_keyboard(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 فتح الفولدر", url=link)],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")],
        ]
    )


def search_results_keyboard(rows: pd.DataFrame) -> InlineKeyboardMarkup:
    buttons = []
    for _, row in rows.iterrows():
        label = f"{row['Name']} | {str(row['Path'])[:28]}"
        buttons.append([InlineKeyboardButton(label[:60], callback_data=f"leaf:{row['Folder_ID']}")])

    buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def format_folder_summary(row: pd.Series) -> str:
    parts = [f"<b>{row['Name']}</b>"]

    top = str(row.get("Top_Section", "")).strip()
    company = str(row.get("Company", "")).strip()
    path = str(row.get("Path", "")).strip()

    if top:
        parts.append(f"القسم: {top}")
    if company and company != "official_docs":
        parts.append(f"الشركة: {company}")
    if path:
        parts.append(f"المسار: {path}")

    parts.append("اضغط الزر لفتح الفولدر.")
    return "\n".join(parts)


async def get_df(context: ContextTypes.DEFAULT_TYPE) -> pd.DataFrame:
    df = context.application.bot_data.get("index_df")
    if df is None:
        df = load_index()
        context.application.bot_data["index_df"] = df
    return df


async def reply_or_edit(update: Update, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
    else:
        await update.message.reply_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["state"] = STATE_IDLE
    text = (
        f"أهلاً بيك في <b>{BOT_TITLE}</b>\n"
        "تقدر تتصفح الفولدرات خطوة بخطوة، أو تبحث مباشرة برقم أمر الصرف."
    )
    await reply_or_edit(update, text, main_menu())


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data or ""
    df = await get_df(context)

    if data == "home":
        await start(update, context)
        return

    if data == "refresh":
        context.application.bot_data["index_df"] = load_index()
        await reply_or_edit(update, "تم تحديث بيانات الشيت ✅", main_menu())
        return

    if data == "browse_root":
        sections = top_sections(df)
        await reply_or_edit(update, "اختار القسم:", folder_buttons(sections))
        return

    if data.startswith("open:"):
        folder_id = data.split(":", 1)[1]
        folder_row_df = find_folder_by_id(df, folder_id)

        if folder_row_df.empty:
            await reply_or_edit(update, "لم أجد هذا الفولدر.", main_menu())
            return

        row = folder_row_df.iloc[0]
        kids = children_of(df, folder_id)
        is_leaf = str(row.get("Is_Leaf", "")).upper() == "YES" or kids.empty

        if is_leaf:
            await reply_or_edit(update, format_folder_summary(row), result_keyboard(str(row["Link"])))
            return

        await reply_or_edit(update, f"<b>{row['Name']}</b>\nاختار اللي بعده:", folder_buttons(kids))
        return

    if data == "search_order":
        companies = unique_companies(df)
        if not companies:
            await reply_or_edit(update, "لم أجد شركات داخل قسم أوامر الصرف.", main_menu())
            return
        await reply_or_edit(update, "اختار الشركة:", companies_keyboard(companies))
        return

    if data.startswith("company:"):
        company = data.split(":", 1)[1]
        context.user_data["search_company"] = company
        context.user_data["state"] = STATE_AWAIT_ORDER_NUMBER

        await reply_or_edit(
            update,
            f"تم اختيار الشركة: <b>{company}</b>\nابعت الآن رقم أمر الصرف.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🏠 الرئيسية", callback_data="home")]]),
        )
        return

    if data.startswith("leaf:"):
        folder_id = data.split(":", 1)[1]
        folder_row_df = find_folder_by_id(df, folder_id)

        if folder_row_df.empty:
            await reply_or_edit(update, "النتيجة غير موجودة.", main_menu())
            return

        row = folder_row_df.iloc[0]
        await reply_or_edit(update, format_folder_summary(row), result_keyboard(str(row["Link"])))
        return

    await reply_or_edit(update, "الخيار غير معروف.", main_menu())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    state = context.user_data.get("state", STATE_IDLE)

    if state == STATE_AWAIT_ORDER_NUMBER:
        company = context.user_data.get("search_company", "")
        if not company:
            context.user_data["state"] = STATE_IDLE
            await update.message.reply_text(
                "اختار الشركة أولاً.",
                reply_markup=main_menu(),
                parse_mode=ParseMode.HTML,
            )
            return

        df = await get_df(context)
        rows = search_order_folders(df, company, text)
        context.user_data["state"] = STATE_IDLE

        if rows.empty:
            await update.message.reply_text(
                f"لم أجد أمر الصرف رقم <b>{text}</b> داخل شركة <b>{company}</b>.",
                reply_markup=main_menu(),
                parse_mode=ParseMode.HTML,
            )
            return

        if len(rows) == 1:
            row = rows.iloc[0]
            await update.message.reply_text(
                format_folder_summary(row),
                reply_markup=result_keyboard(str(row["Link"])),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            return

        await update.message.reply_text(
            f"وجدت أكثر من نتيجة لرقم <b>{text}</b> داخل <b>{company}</b>. اختر النتيجة:",
            reply_markup=search_results_keyboard(rows),
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        "اضغط /start أو اختار من القايمة.",
        reply_markup=main_menu(),
        parse_mode=ParseMode.HTML,
    )


def main() -> None:
    check_env()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Drive sheet archive bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
