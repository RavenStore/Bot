# -*- coding: utf-8 -*-
"""
================================================================================
 AXENTRA SELLER BOT — Premium Telegram Dijital Ürün Satış Botu
================================================================================
Tek dosyalık (main.py), üretime hazır Telegram dijital ürün mağazası botu.

Çalıştırma:
    pip install aiogram sqlalchemy aiosqlite aiohttp
    python main.py

Aşağıdaki "AYARLAR" bölümünü kendi bilgilerinizle doldurun.
================================================================================
"""

import asyncio
import csv
import io
import json
import logging
import os
import shutil
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    BufferedInputFile,
    Update,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Boolean, DateTime, ForeignKey,
    Text, select, func, delete as sa_delete, update as sa_update
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship, selectinload

from aiohttp import web

# ==============================================================================
# AYARLAR (BURAYI DOLDURUN)
# ==============================================================================
BOT_TOKEN = "8987358112:AAEe8vP7cPcMPJe6AC3KlWII6O6vkxW799I"           # @BotFather'dan alınan token
OWNER_IDS = [8561815348]                        # Botun sahibi olan Telegram ID'ler (int liste)

DB_PATH = "database.db"                        # SQLite dosya adı (otomatik oluşturulur)
BACKUP_DIR = "backups"                         # Yedeklerin tutulacağı klasör

CURRENCY = "₺"                                 # Para birimi simgesi

RATE_LIMIT_COUNT = 8                           # Belirli sürede izin verilen maksimum işlem
RATE_LIMIT_WINDOW = 10                         # saniye

WEB_ENABLED = True                             # Web istatistik paneli açık/kapalı
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080
WEB_TOKEN = "degistir-bu-gizli-token"          # Web paneline erişim anahtarı (?token=...)

SUPPORT_USERNAME = "@destek"                   # Destek ekranında gösterilecek iletişim

TAGS = {
    "yeni": "🆕 Yeni",
    "populer": "🔥 Popüler",
    "guncel": "♻️ Güncellendi",
    "yakinda": "⏳ Yakında",
    "tukeniyor": "⚠️ Tükeniyor",
    "none": "",
}

# ==============================================================================
# LOGLAMA
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("axentra")

# ==============================================================================
# VERİTABANI MODELLERİ
# ==============================================================================
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)  # telegram user id
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), nullable=True)
    last_name = Column(String(128), nullable=True)
    balance = Column(Float, default=0.0)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    total_orders = Column(Integer, default=0)
    total_spent = Column(Float, default=0.0)


class Admin(Base):
    __tablename__ = "admins"
    user_id = Column(BigInteger, primary_key=True)
    role = Column(String(32), default="admin")  # owner / admin / moderator
    added_by = Column(BigInteger, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(128))
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    is_hidden = Column(Boolean, default=False)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"))
    name = Column(String(160))
    description = Column(Text, default="")
    features = Column(Text, default="")
    price = Column(Float, default=0.0)
    old_price = Column(Float, nullable=True)
    tag = Column(String(32), default="none")
    images = Column(Text, default="[]")  # json list of telegram file_id
    video_file_id = Column(String(256), nullable=True)
    is_active = Column(Boolean, default=True)
    is_hidden = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Stock(Base):
    __tablename__ = "stock"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    content = Column(Text)
    is_delivered = Column(Boolean, default=False)
    delivered_to = Column(BigInteger, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    total_amount = Column(Float, default=0.0)
    status = Column(String(32), default="completed")  # completed / cancelled
    created_at = Column(DateTime, default=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name = Column(String(160))
    price = Column(Float)
    stock_id = Column(Integer, ForeignKey("stock.id"), nullable=True)


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    subject = Column(String(160), default="Destek Talebi")
    status = Column(String(16), default="open")  # open / closed
    created_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    last_message_at = Column(DateTime, default=datetime.utcnow)
    unread_by_admin = Column(Boolean, default=True)
    unread_by_user = Column(Boolean, default=False)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id"))
    sender = Column(String(16))  # user / admin
    text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Announcement(Base):
    __tablename__ = "announcements"
    id = Column(Integer, primary_key=True)
    text = Column(Text)
    scheduled_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    status = Column(String(16), default="pending")  # pending / sent / cancelled
    created_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BalanceRequest(Base):
    __tablename__ = "balance_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    amount = Column(Float)
    status = Column(String(16), default="pending")  # pending / approved / rejected
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AdminLog(Base):
    __tablename__ = "admin_logs"
    id = Column(Integer, primary_key=True)
    admin_id = Column(BigInteger)
    action = Column(String(64))
    details = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(Text, default="")


engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        for oid in OWNER_IDS:
            existing = await s.get(Admin, oid)
            if not existing:
                s.add(Admin(user_id=oid, role="owner", added_by=oid))
        for key, default in (("maintenance_mode", "0"), ("support_text", SUPPORT_USERNAME)):
            st = await s.get(Setting, key)
            if not st:
                s.add(Setting(key=key, value=default))
        await s.commit()


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    st = await session.get(Setting, key)
    return st.value if st else default


async def set_setting(session: AsyncSession, key: str, value: str):
    st = await session.get(Setting, key)
    if st:
        st.value = value
    else:
        session.add(Setting(key=key, value=value))
    await session.commit()


async def log_action(session: AsyncSession, admin_id: int, action: str, details: str = ""):
    session.add(AdminLog(admin_id=admin_id, action=action, details=details))
    await session.commit()


async def is_admin(session: AsyncSession, user_id: int) -> Optional[Admin]:
    return await session.get(Admin, user_id)


# ==============================================================================
# YARDIMCI FONKSİYONLAR / KLAVYELER
# ==============================================================================

def money(v: float) -> str:
    return f"{v:,.2f} {CURRENCY}".replace(",", "X").replace(".", ",").replace("X", ".")


def main_menu_kb(is_admin_user: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🛒 Ürünler", callback_data="m:products")
    b.button(text="📦 Siparişlerim", callback_data="m:orders")
    b.button(text="🔍 Lisans Sorgula", callback_data="m:license")
    b.button(text="📢 Duyurular", callback_data="m:announcements")
    b.button(text="💬 Destek", callback_data="m:support")
    b.button(text="👤 Hesabım", callback_data="m:account")
    b.adjust(2, 2, 2)
    if is_admin_user:
        b.row(InlineKeyboardButton(text="🛠 Admin Paneli", callback_data="a:dashboard"))
    return b.as_markup()


def back_kb(target: str, text: str = "◀️ Geri") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, callback_data=target)
    return b.as_markup()


WELCOME_TEXT = (
    "✨ <b>AXENTRA</b>'ya hoş geldiniz\n\n"
    "Premium dijital ürün mağazasına bağlandınız. Aşağıdaki menüden "
    "işlem seçebilirsiniz."
)


async def show_main_menu(target, session: AsyncSession, user_id: int, edit: bool = True):
    admin_row = await is_admin(session, user_id)
    kb = main_menu_kb(admin_row is not None)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(WELCOME_TEXT, reply_markup=kb)
        except TelegramBadRequest:
            await target.message.answer(WELCOME_TEXT, reply_markup=kb)
    else:
        await target.answer(WELCOME_TEXT, reply_markup=kb)


async def safe_edit(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup = None):
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        try:
            await cb.message.answer(text, reply_markup=kb)
        except Exception:
            pass


# ==============================================================================
# FSM DURUMLARI
# ==============================================================================
class CategoryForm(StatesGroup):
    name = State()
    edit_name = State()


class ProductForm(StatesGroup):
    category = State()
    name = State()
    description = State()
    features = State()
    price = State()
    old_price = State()
    tag = State()
    images = State()
    edit_field_value = State()


class StockForm(StatesGroup):
    bulk_add = State()


class SearchForm(StatesGroup):
    user_query = State()
    license_query = State()


class SupportForm(StatesGroup):
    subject = State()
    message = State()
    admin_reply = State()


class AnnouncementForm(StatesGroup):
    text = State()
    schedule = State()


class BalanceForm(StatesGroup):
    amount = State()
    admin_note_reject = State()


class UserAdminForm(StatesGroup):
    ban_reason = State()
    note = State()
    add_balance = State()


class AdminManageForm(StatesGroup):
    add_id = State()


class SettingsForm(StatesGroup):
    support_text = State()


# ==============================================================================
# ORTA KATMANLAR (MIDDLEWARE): RATE LIMIT + BAN + BAKIM MODU
# ==============================================================================
_hit_log: dict[int, deque] = defaultdict(deque)


class GuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Update, data):
        user = None
        if event.message:
            user = event.message.from_user
        elif event.callback_query:
            user = event.callback_query.from_user

        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        dq = _hit_log[user.id]
        dq.append(now)
        while dq and now - dq[0] > RATE_LIMIT_WINDOW:
            dq.popleft()
        if len(dq) > RATE_LIMIT_COUNT:
            if event.callback_query:
                await event.callback_query.answer("⏳ Çok hızlısınız, birazdan tekrar deneyin.", show_alert=False)
            return

        async with async_session() as session:
            admin_row = await is_admin(session, user.id)
            db_user = await session.get(User, user.id)
            if db_user is None:
                db_user = User(
                    id=user.id, username=user.username,
                    first_name=user.first_name, last_name=user.last_name,
                )
                session.add(db_user)
                await session.commit()
            else:
                db_user.username = user.username
                db_user.first_name = user.first_name
                db_user.last_name = user.last_name
                db_user.last_seen = datetime.utcnow()
                await session.commit()

            if db_user.is_banned:
                text = f"🚫 Hesabınız engellenmiştir.\nSebep: {db_user.ban_reason or 'belirtilmedi'}"
                if event.callback_query:
                    await event.callback_query.answer(text, show_alert=True)
                elif event.message:
                    await event.message.answer(text)
                return

            maintenance = await get_setting(session, "maintenance_mode", "0")
            if maintenance == "1" and not admin_row:
                text = "🛠 Bot şu anda bakım modunda. Lütfen daha sonra tekrar deneyin."
                if event.callback_query:
                    await event.callback_query.answer(text, show_alert=True)
                elif event.message:
                    await event.message.answer(text)
                return

        return await handler(event, data)


# ==============================================================================
# ROUTER'LAR
# ==============================================================================
user_router = Router()
admin_router = Router()

ADMIN_ONLY_PREFIXES = ("a:",)


@admin_router.callback_query(F.data.startswith("a:"))
async def admin_gate(cb: CallbackQuery, state: FSMContext):
    async with async_session() as s:
        row = await is_admin(s, cb.from_user.id)
        if not row:
            await cb.answer("Yetkiniz yok.", show_alert=True)
            return
    await route_admin(cb, state, row.role)


# ------------------------------------------------------------------------------
# /start ve ana menü
# ------------------------------------------------------------------------------
@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    async with async_session() as s:
        await show_main_menu(message, s, message.from_user.id)


@user_router.callback_query(F.data == "m:main")
async def cb_main(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    async with async_session() as s:
        await show_main_menu(cb, s, cb.from_user.id)
    await cb.answer()


# ------------------------------------------------------------------------------
# ÜRÜNLER (kullanıcı tarafı)
# ------------------------------------------------------------------------------
@user_router.callback_query(F.data == "m:products")
async def cb_products(cb: CallbackQuery):
    async with async_session() as s:
        cats = (await s.execute(
            select(Category).where(Category.is_active == True, Category.is_hidden == False)
            .order_by(Category.sort_order)
        )).scalars().all()
    b = InlineKeyboardBuilder()
    if not cats:
        b.button(text="◀️ Ana Menü", callback_data="m:main")
        await safe_edit(cb, "🛒 Şu anda satışta kategori bulunmuyor.", b.as_markup())
        await cb.answer()
        return
    for c in cats:
        b.button(text=f"📁 {c.name}", callback_data=f"m:cat:{c.id}:0")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="◀️ Ana Menü", callback_data="m:main"))
    await safe_edit(cb, "🛒 <b>Ürün Kategorileri</b>\n\nBir kategori seçin:", b.as_markup())
    await cb.answer()


PAGE_SIZE = 5


@user_router.callback_query(F.data.startswith("m:cat:"))
async def cb_category(cb: CallbackQuery):
    _, _, cat_id, page = cb.data.split(":")
    cat_id, page = int(cat_id), int(page)
    async with async_session() as s:
        cat = await s.get(Category, cat_id)
        products = (await s.execute(
            select(Product).where(
                Product.category_id == cat_id,
                Product.is_active == True, Product.is_hidden == False,
            )
        )).scalars().all()

    b = InlineKeyboardBuilder()
    total = len(products)
    start = page * PAGE_SIZE
    chunk = products[start:start + PAGE_SIZE]
    if not chunk:
        b.button(text="◀️ Kategoriler", callback_data="m:products")
        await safe_edit(cb, f"📁 <b>{cat.name if cat else ''}</b>\n\nBu kategoride ürün yok.", b.as_markup())
        await cb.answer()
        return

    for p in chunk:
        tag = TAGS.get(p.tag, "")
        label = f"{p.name} — {money(p.price)}"
        if tag:
            label = f"{tag} {label}"
        b.button(text=label, callback_data=f"m:prod:{p.id}")
    b.adjust(1)

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"m:cat:{cat_id}:{page-1}"))
    if start + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"m:cat:{cat_id}:{page+1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="◀️ Kategoriler", callback_data="m:products"))

    await safe_edit(cb, f"📁 <b>{cat.name if cat else ''}</b>\n\nBir ürün seçin:", b.as_markup())
    await cb.answer()


@user_router.callback_query(F.data.startswith("m:prod:"))
async def cb_product_detail(cb: CallbackQuery):
    product_id = int(cb.data.split(":")[2])
    async with async_session() as s:
        p = await s.get(Product, product_id)
        if not p:
            await cb.answer("Ürün bulunamadı.", show_alert=True)
            return
        stock_count = (await s.execute(
            select(func.count()).select_from(Stock).where(
                Stock.product_id == product_id, Stock.is_delivered == False
            )
        )).scalar()

    tag = TAGS.get(p.tag, "")
    price_line = money(p.price)
    if p.old_price and p.old_price > p.price:
        price_line = f"<s>{money(p.old_price)}</s>  <b>{money(p.price)}</b>"
    else:
        price_line = f"<b>{price_line}</b>"

    text = (
        f"{tag + chr(10) if tag else ''}"
        f"🛍 <b>{p.name}</b>\n\n"
        f"{p.description or ''}\n\n"
        f"{('✨ ' + p.features) if p.features else ''}\n\n"
        f"💰 Fiyat: {price_line}\n"
        f"📦 Stok: {'✅ Mevcut (' + str(stock_count) + ')' if stock_count > 0 else '❌ Tükendi'}"
    )

    b = InlineKeyboardBuilder()
    if stock_count > 0:
        b.button(text=f"✅ Satın Al — {money(p.price)}", callback_data=f"m:buy:{p.id}")
    else:
        b.button(text="❌ Stokta Yok", callback_data="noop")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="◀️ Geri", callback_data=f"m:cat:{p.category_id}:0"))

    images = json.loads(p.images or "[]")
    if images:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer_photo(images[0], caption=text, reply_markup=b.as_markup())
    else:
        await safe_edit(cb, text, b.as_markup())
    await cb.answer()


@user_router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@user_router.callback_query(F.data.startswith("m:buy:"))
async def cb_buy_confirm(cb: CallbackQuery):
    product_id = int(cb.data.split(":")[2])
    async with async_session() as s:
        p = await s.get(Product, product_id)
        u = await s.get(User, cb.from_user.id)
        if not p or not p.is_active:
            await cb.answer("Ürün artık mevcut değil.", show_alert=True)
            return
    b = InlineKeyboardBuilder()
    b.button(text="✅ Onayla ve Satın Al", callback_data=f"m:buyok:{product_id}")
    b.button(text="❌ Vazgeç", callback_data=f"m:prod:{product_id}")
    b.adjust(1)
    await safe_edit(
        cb,
        f"🛒 <b>{p.name}</b>\n\nTutar: <b>{money(p.price)}</b>\nBakiyeniz: {money(u.balance)}\n\n"
        f"Satın alma işlemini onaylıyor musunuz?",
        b.as_markup(),
    )
    await cb.answer()


@user_router.callback_query(F.data.startswith("m:buyok:"))
async def cb_buy_execute(cb: CallbackQuery):
    product_id = int(cb.data.split(":")[2])
    async with async_session() as s:
        p = await s.get(Product, product_id)
        u = await s.get(User, cb.from_user.id)
        if not p or not p.is_active:
            await cb.answer("Ürün artık mevcut değil.", show_alert=True)
            return
        if u.balance < p.price:
            b = InlineKeyboardBuilder()
            b.button(text="💳 Bakiye Yükle", callback_data="m:topup")
            b.button(text="◀️ Geri", callback_data=f"m:prod:{product_id}")
            b.adjust(1)
            await safe_edit(cb, "❌ Bakiyeniz yetersiz.", b.as_markup())
            await cb.answer()
            return

        stock_row = (await s.execute(
            select(Stock).where(Stock.product_id == product_id, Stock.is_delivered == False)
            .order_by(Stock.id).limit(1)
        )).scalar_one_or_none()
        if not stock_row:
            await cb.answer("❌ Stok tükendi.", show_alert=True)
            return

        u.balance -= p.price
        u.total_orders += 1
        u.total_spent += p.price
        order = Order(user_id=u.id, total_amount=p.price, status="completed")
        s.add(order)
        await s.flush()
        stock_row.is_delivered = True
        stock_row.delivered_to = u.id
        stock_row.delivered_at = datetime.utcnow()
        item = OrderItem(order_id=order.id, product_id=p.id, product_name=p.name,
                          price=p.price, stock_id=stock_row.id)
        s.add(item)
        await s.commit()
        content = stock_row.content
        order_id = order.id

    b = InlineKeyboardBuilder()
    b.button(text="📦 Siparişlerim", callback_data="m:orders")
    b.button(text="◀️ Ana Menü", callback_data="m:main")
    b.adjust(1)
    await safe_edit(
        cb,
        f"✅ <b>Satın alma başarılı!</b>\n\nSipariş No: #{order_id}\n\n"
        f"📦 <b>Teslim Edilen İçerik:</b>\n<code>{content}</code>",
        b.as_markup(),
    )
    await cb.answer("Teslimat tamamlandı ✅")


# ------------------------------------------------------------------------------
# SİPARİŞLERİM
# ------------------------------------------------------------------------------
@user_router.callback_query(F.data == "m:orders")
async def cb_orders(cb: CallbackQuery):
    async with async_session() as s:
        orders = (await s.execute(
            select(Order).where(Order.user_id == cb.from_user.id).order_by(Order.id.desc()).limit(20)
        )).scalars().all()
    b = InlineKeyboardBuilder()
    if not orders:
        b.button(text="◀️ Ana Menü", callback_data="m:main")
        await safe_edit(cb, "📦 Henüz siparişiniz bulunmuyor.", b.as_markup())
        await cb.answer()
        return
    for o in orders:
        status_icon = {"completed": "✅", "cancelled": "❌", "pending": "⏳"}.get(o.status, "•")
        b.button(text=f"{status_icon} #{o.id} — {money(o.total_amount)}", callback_data=f"m:order:{o.id}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="◀️ Ana Menü", callback_data="m:main"))
    await safe_edit(cb, "📦 <b>Siparişlerim</b>", b.as_markup())
    await cb.answer()


@user_router.callback_query(F.data.startswith("m:order:"))
async def cb_order_detail(cb: CallbackQuery):
    order_id = int(cb.data.split(":")[2])
    async with async_session() as s:
        o = await s.get(Order, order_id)
        if not o or o.user_id != cb.from_user.id:
            await cb.answer("Sipariş bulunamadı.", show_alert=True)
            return
        items = (await s.execute(select(OrderItem).where(OrderItem.order_id == order_id))).scalars().all()
        lines = []
        for it in items:
            stock_row = await s.get(Stock, it.stock_id) if it.stock_id else None
            content = stock_row.content if stock_row else "—"
            lines.append(f"• <b>{it.product_name}</b> — {money(it.price)}\n  <code>{content}</code>")

    text = (
        f"📦 <b>Sipariş #{o.id}</b>\n"
        f"Tarih: {o.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"Durum: {o.status}\n"
        f"Toplam: {money(o.total_amount)}\n\n" + "\n\n".join(lines)
    )
    b = back_kb("m:orders")
    await safe_edit(cb, text, b)
    await cb.answer()


# ------------------------------------------------------------------------------
# LİSANS SORGULA
# ------------------------------------------------------------------------------
@user_router.callback_query(F.data == "m:license")
async def cb_license_prompt(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SearchForm.license_query)
    await safe_edit(cb, "🔍 Sorgulamak istediğiniz lisans/anahtar veya sipariş numarasını yazın:",
                     back_kb("m:main"))
    await cb.answer()


@user_router.message(SearchForm.license_query)
async def msg_license_query(message: Message, state: FSMContext):
    q = message.text.strip()
    await state.clear()
    async with async_session() as s:
        stock_row = (await s.execute(
            select(Stock).where(Stock.delivered_to == message.from_user.id, Stock.content.like(f"%{q}%"))
        )).scalars().first()
    if stock_row:
        text = (
            f"✅ <b>Lisans bulundu</b>\n\n<code>{stock_row.content}</code>\n"
            f"Teslim tarihi: {stock_row.delivered_at.strftime('%d.%m.%Y %H:%M') if stock_row.delivered_at else '-'}"
        )
    else:
        text = "❌ Bu sorgu ile eşleşen bir lisans bulunamadı."
    await message.answer(text, reply_markup=back_kb("m:main"))


# ------------------------------------------------------------------------------
# DUYURULAR (kullanıcı görünümü)
# ------------------------------------------------------------------------------
@user_router.callback_query(F.data == "m:announcements")
async def cb_announcements(cb: CallbackQuery):
    async with async_session() as s:
        anns = (await s.execute(
            select(Announcement).where(Announcement.status == "sent")
            .order_by(Announcement.sent_at.desc()).limit(10)
        )).scalars().all()
    if not anns:
        text = "📢 Henüz duyuru bulunmuyor."
    else:
        parts = []
        for a in anns:
            parts.append(f"🗓 {a.sent_at.strftime('%d.%m.%Y %H:%M')}\n{a.text}")
        text = "📢 <b>Duyurular</b>\n\n" + "\n\n➖➖➖\n\n".join(parts)
    await safe_edit(cb, text, back_kb("m:main"))
    await cb.answer()


# ------------------------------------------------------------------------------
# DESTEK (kullanıcı tarafı)
# ------------------------------------------------------------------------------
@user_router.callback_query(F.data == "m:support")
async def cb_support(cb: CallbackQuery):
    async with async_session() as s:
        ticket = (await s.execute(
            select(SupportTicket).where(
                SupportTicket.user_id == cb.from_user.id, SupportTicket.status == "open"
            )
        )).scalars().first()
        support_text = await get_setting(s, "support_text", SUPPORT_USERNAME)

    b = InlineKeyboardBuilder()
    if ticket:
        b.button(text="✉️ Devam Et", callback_data=f"m:tick:{ticket.id}")
        b.button(text="🔒 Talebi Kapat", callback_data=f"m:tickclose:{ticket.id}")
        text = f"💬 Açık destek talebiniz bulunuyor (#{ticket.id})."
    else:
        b.button(text="🆕 Yeni Destek Talebi", callback_data="m:supportnew")
        text = f"💬 <b>Destek</b>\n\nİletişim: {support_text}\n\nBir talep oluşturabilirsiniz."
    b.adjust(1)
    b.row(InlineKeyboardButton(text="◀️ Ana Menü", callback_data="m:main"))
    await safe_edit(cb, text, b.as_markup())
    await cb.answer()


@user_router.callback_query(F.data == "m:supportnew")
async def cb_support_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SupportForm.subject)
    await safe_edit(cb, "📝 Talebiniz için kısa bir başlık yazın:", back_kb("m:support"))
    await cb.answer()


@user_router.message(SupportForm.subject)
async def msg_support_subject(message: Message, state: FSMContext):
    async with async_session() as s:
        ticket = SupportTicket(user_id=message.from_user.id, subject=message.text.strip()[:160])
        s.add(ticket)
        await s.commit()
        ticket_id = ticket.id
    await state.set_state(SupportForm.message)
    await state.update_data(ticket_id=ticket_id)
    await message.answer("✍️ Şimdi mesajınızı yazın:")


@user_router.message(SupportForm.message)
async def msg_support_message(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data["ticket_id"]
    async with async_session() as s:
        s.add(SupportMessage(ticket_id=ticket_id, sender="user", text=message.text))
        t = await s.get(SupportTicket, ticket_id)
        t.last_message_at = datetime.utcnow()
        t.unread_by_admin = True
        await s.commit()
    await state.clear()
    await message.answer(f"✅ Destek talebiniz alındı (#{ticket_id}). En kısa sürede yanıtlanacaktır.",
                          reply_markup=back_kb("m:main"))


@user_router.callback_query(F.data.startswith("m:tick:"))
async def cb_ticket_view(cb: CallbackQuery, state: FSMContext):
    ticket_id = int(cb.data.split(":")[2])
    async with async_session() as s:
        t = await s.get(SupportTicket, ticket_id)
        if not t or t.user_id != cb.from_user.id:
            await cb.answer("Bulunamadı.", show_alert=True)
            return
        msgs = (await s.execute(
            select(SupportMessage).where(SupportMessage.ticket_id == ticket_id).order_by(SupportMessage.id)
        )).scalars().all()
        t.unread_by_user = False
        await s.commit()
    lines = [f"{'👤 Siz' if m.sender=='user' else '🛠 Destek'}: {m.text}" for m in msgs]
    text = f"💬 <b>Destek #{ticket_id}</b> — {t.subject}\n\n" + "\n\n".join(lines[-15:])
    await state.set_state(SupportForm.message)
    await state.update_data(ticket_id=ticket_id)
    await safe_edit(cb, text + "\n\n✍️ Yanıt yazmak için mesaj gönderin.", back_kb("m:support"))
    await cb.answer()


@user_router.callback_query(F.data.startswith("m:tickclose:"))
async def cb_ticket_close(cb: CallbackQuery):
    ticket_id = int(cb.data.split(":")[2])
    async with async_session() as s:
        t = await s.get(SupportTicket, ticket_id)
        if t and t.user_id == cb.from_user.id:
            t.status = "closed"
            t.closed_at = datetime.utcnow()
            await s.commit()
    await cb.answer("Talep kapatıldı.")
    await cb_support(cb)


# ------------------------------------------------------------------------------
# HESABIM
# ------------------------------------------------------------------------------
@user_router.callback_query(F.data == "m:account")
async def cb_account(cb: CallbackQuery):
    async with async_session() as s:
        u = await s.get(User, cb.from_user.id)
    text = (
        f"👤 <b>Hesabım</b>\n\n"
        f"Ad: {u.first_name or '-'} {u.last_name or ''}\n"
        f"Kullanıcı adı: @{u.username or '-'}\n"
        f"Telegram ID: <code>{u.id}</code>\n"
        f"Kayıt tarihi: {u.created_at.strftime('%d.%m.%Y')}\n"
        f"Son giriş: {u.last_seen.strftime('%d.%m.%Y %H:%M')}\n"
        f"💰 Bakiye: <b>{money(u.balance)}</b>\n"
        f"📦 Toplam sipariş: {u.total_orders}\n"
        f"💸 Toplam harcama: {money(u.total_spent)}"
    )
    b = InlineKeyboardBuilder()
    b.button(text="💳 Bakiye Yükle", callback_data="m:topup")
    b.button(text="◀️ Ana Menü", callback_data="m:main")
    b.adjust(1)
    await safe_edit(cb, text, b.as_markup())
    await cb.answer()


@user_router.callback_query(F.data == "m:topup")
async def cb_topup(cb: CallbackQuery, state: FSMContext):
    await state.set_state(BalanceForm.amount)
    await safe_edit(cb, f"💳 Yüklemek istediğiniz miktarı yazın (sadece sayı, {CURRENCY}):",
                     back_kb("m:account"))
    await cb.answer()


@user_router.message(BalanceForm.amount)
async def msg_topup_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", ".").strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Geçerli bir tutar girin.")
        return
    await state.clear()
    async with async_session() as s:
        req = BalanceRequest(user_id=message.from_user.id, amount=amount)
        s.add(req)
        await s.commit()
        req_id = req.id
    await message.answer(
        f"✅ Bakiye yükleme talebiniz alındı (#{req_id}). Yönetici onayından sonra bakiyenize eklenecektir.",
        reply_markup=back_kb("m:main"),
    )
    for oid in OWNER_IDS:
        try:
            await message.bot.send_message(
                oid, f"💳 Yeni bakiye talebi #{req_id}\nKullanıcı: {message.from_user.id}\nTutar: {money(amount)}"
            )
        except Exception:
            pass


# ==============================================================================
# ADMİN PANELİ
# ==============================================================================

async def route_admin(cb: CallbackQuery, state: FSMContext, role: str):
    action = cb.data[2:]  # "a:" sonrası
    parts = action.split(":")
    key = parts[0]

    handlers = {
        "dashboard": admin_dashboard,
        "categories": admin_categories,
        "products": admin_products,
        "stock": admin_stock,
        "orders": admin_orders,
        "users": admin_users,
        "balance": admin_balance_requests,
        "announcements": admin_announcements,
        "support": admin_support,
        "logs": admin_logs,
        "backup": admin_backup,
        "maintenance": admin_maintenance_toggle,
        "admins": admin_manage_admins,
        "settings": admin_settings,
    }

    if key in handlers:
        await handlers[key](cb, state, parts)
    else:
        await cb.answer("Bilinmeyen işlem.", show_alert=True)


def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Dashboard", callback_data="a:dashboard")
    b.button(text="📁 Kategoriler", callback_data="a:categories:list")
    b.button(text="🛍 Ürünler", callback_data="a:products:list")
    b.button(text="📦 Stok", callback_data="a:stock:list")
    b.button(text="🧾 Siparişler", callback_data="a:orders:list:0")
    b.button(text="👥 Kullanıcılar", callback_data="a:users:menu")
    b.button(text="💳 Bakiye Talepleri", callback_data="a:balance:list")
    b.button(text="📢 Duyurular", callback_data="a:announcements:list")
    b.button(text="💬 Destek", callback_data="a:support:list")
    b.button(text="🧾 Loglar", callback_data="a:logs:0")
    b.button(text="💾 Yedekleme", callback_data="a:backup:menu")
    b.button(text="🛠 Bakım Modu", callback_data="a:maintenance:toggle")
    b.button(text="👑 Yöneticiler", callback_data="a:admins:list")
    b.button(text="⚙️ Ayarlar", callback_data="a:settings:menu")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="◀️ Ana Menü", callback_data="m:main"))
    return b.as_markup()


async def admin_dashboard(cb: CallbackQuery, state: FSMContext, parts):
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    async with async_session() as s:
        total_users = (await s.execute(select(func.count()).select_from(User))).scalar()
        today_users = (await s.execute(
            select(func.count()).select_from(User).where(User.created_at >= today_start)
        )).scalar()
        total_orders = (await s.execute(select(func.count()).select_from(Order))).scalar()
        today_orders = (await s.execute(
            select(func.count()).select_from(Order).where(Order.created_at >= today_start)
        )).scalar()
        total_revenue = (await s.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.status == "completed")
        )).scalar()
        today_revenue = (await s.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.status == "completed", Order.created_at >= today_start
            )
        )).scalar()
        active_products = (await s.execute(
            select(func.count()).select_from(Product).where(Product.is_active == True)
        )).scalar()
        passive_products = (await s.execute(
            select(func.count()).select_from(Product).where(Product.is_active == False)
        )).scalar()
        cat_count = (await s.execute(select(func.count()).select_from(Category))).scalar()
        open_tickets = (await s.execute(
            select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "open")
        )).scalar()

    text = (
        "📊 <b>Admin Dashboard</b>\n\n"
        f"👥 Toplam kullanıcı: <b>{total_users}</b> (bugün +{today_users})\n"
        f"🧾 Toplam sipariş: <b>{total_orders}</b> (bugün +{today_orders})\n"
        f"💰 Toplam gelir: <b>{money(total_revenue)}</b> (bugün {money(today_revenue)})\n"
        f"🛍 Aktif ürün: {active_products} | Pasif ürün: {passive_products}\n"
        f"📁 Kategori sayısı: {cat_count}\n"
        f"💬 Açık destek talebi: {open_tickets}\n"
    )
    if WEB_ENABLED:
        text += f"\n🌐 Detaylı grafikli panel: http://{WEB_HOST}:{WEB_PORT}/?token={WEB_TOKEN}"
    await safe_edit(cb, text, admin_menu_kb())
    await cb.answer()


# --- Kategori Yönetimi -------------------------------------------------------
async def admin_categories(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        async with async_session() as s:
            cats = (await s.execute(select(Category).order_by(Category.sort_order))).scalars().all()
        b = InlineKeyboardBuilder()
        for c in cats:
            status = "✅" if c.is_active else "⛔️"
            hidden = "🙈" if c.is_hidden else ""
            b.button(text=f"{status}{hidden} {c.name}", callback_data=f"a:categories:view:{c.id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="➕ Yeni Kategori", callback_data="a:categories:new"))
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "📁 <b>Kategori Yönetimi</b>", b.as_markup())
        await cb.answer()
    elif sub == "new":
        await state.set_state(CategoryForm.name)
        await safe_edit(cb, "📁 Yeni kategori adını yazın:", back_kb("a:categories:list"))
        await cb.answer()
    elif sub == "view":
        cat_id = int(parts[2])
        async with async_session() as s:
            c = await s.get(Category, cat_id)
        b = InlineKeyboardBuilder()
        b.button(text="✏️ Ad Değiştir", callback_data=f"a:categories:rename:{cat_id}")
        b.button(text="🔀 Sıra +1", callback_data=f"a:categories:sortup:{cat_id}")
        b.button(text=("🚫 Pasif Yap" if c.is_active else "✅ Aktif Yap"),
                  callback_data=f"a:categories:toggle:{cat_id}")
        b.button(text=("👁 Göster" if c.is_hidden else "🙈 Gizle"),
                  callback_data=f"a:categories:hide:{cat_id}")
        b.button(text="🗑 Sil", callback_data=f"a:categories:delask:{cat_id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Liste", callback_data="a:categories:list"))
        await safe_edit(cb, f"📁 <b>{c.name}</b>\nSıra: {c.sort_order} | Aktif: {c.is_active} | Gizli: {c.is_hidden}",
                         b.as_markup())
        await cb.answer()
    elif sub == "rename":
        await state.set_state(CategoryForm.edit_name)
        await state.update_data(cat_id=int(parts[2]))
        await safe_edit(cb, "✏️ Yeni kategori adını yazın:", back_kb(f"a:categories:view:{parts[2]}"))
        await cb.answer()
    elif sub == "sortup":
        cat_id = int(parts[2])
        async with async_session() as s:
            c = await s.get(Category, cat_id)
            c.sort_order += 1
            await s.commit()
        await cb.answer("Sıra güncellendi.")
        await admin_categories(cb, state, ["categories", "view", str(cat_id)])
    elif sub == "toggle":
        cat_id = int(parts[2])
        async with async_session() as s:
            c = await s.get(Category, cat_id)
            c.is_active = not c.is_active
            await s.commit()
            await log_action(s, cb.from_user.id, "category_toggle", c.name)
        await cb.answer("Durum güncellendi.")
        await admin_categories(cb, state, ["categories", "view", str(cat_id)])
    elif sub == "hide":
        cat_id = int(parts[2])
        async with async_session() as s:
            c = await s.get(Category, cat_id)
            c.is_hidden = not c.is_hidden
            await s.commit()
        await cb.answer("Görünürlük güncellendi.")
        await admin_categories(cb, state, ["categories", "view", str(cat_id)])
    elif sub == "delask":
        cat_id = int(parts[2])
        b = InlineKeyboardBuilder()
        b.button(text="⚠️ Evet, Sil", callback_data=f"a:categories:delok:{cat_id}")
        b.button(text="❌ Vazgeç", callback_data=f"a:categories:view:{cat_id}")
        b.adjust(1)
        await safe_edit(cb, "Bu kategoriyi ve içindeki tüm ürünleri silmek istediğinize emin misiniz?",
                         b.as_markup())
        await cb.answer()
    elif sub == "delok":
        cat_id = int(parts[2])
        async with async_session() as s:
            c = await s.get(Category, cat_id)
            name = c.name if c else "?"
            prod_ids = [p.id for p in (await s.execute(
                select(Product).where(Product.category_id == cat_id))).scalars().all()]
            for pid in prod_ids:
                await s.execute(sa_delete(Stock).where(Stock.product_id == pid))
            await s.execute(sa_delete(Product).where(Product.category_id == cat_id))
            await s.execute(sa_delete(Category).where(Category.id == cat_id))
            await s.commit()
            await log_action(s, cb.from_user.id, "category_delete", name)
        await cb.answer("Silindi.")
        await admin_categories(cb, state, ["categories", "list"])


@admin_router.message(CategoryForm.name)
async def msg_new_category(message: Message, state: FSMContext):
    async with async_session() as s:
        row = await is_admin(s, message.from_user.id)
        if not row:
            return
        s.add(Category(name=message.text.strip()[:128]))
        await s.commit()
        await log_action(s, message.from_user.id, "category_create", message.text.strip())
    await state.clear()
    await message.answer("✅ Kategori oluşturuldu.", reply_markup=admin_menu_kb())


@admin_router.message(CategoryForm.edit_name)
async def msg_rename_category(message: Message, state: FSMContext):
    data = await state.get_data()
    async with async_session() as s:
        c = await s.get(Category, data["cat_id"])
        c.name = message.text.strip()[:128]
        await s.commit()
    await state.clear()
    await message.answer("✅ Kategori adı güncellendi.", reply_markup=admin_menu_kb())


# --- Ürün Yönetimi ------------------------------------------------------------
async def admin_products(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        async with async_session() as s:
            products = (await s.execute(select(Product).order_by(Product.id.desc()).limit(30))).scalars().all()
        b = InlineKeyboardBuilder()
        for p in products:
            status = "✅" if p.is_active else "⛔️"
            b.button(text=f"{status} {p.name}", callback_data=f"a:products:view:{p.id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="➕ Yeni Ürün", callback_data="a:products:new"))
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "🛍 <b>Ürün Yönetimi</b>", b.as_markup())
        await cb.answer()
    elif sub == "new":
        async with async_session() as s:
            cats = (await s.execute(select(Category))).scalars().all()
        if not cats:
            await cb.answer("Önce bir kategori oluşturun.", show_alert=True)
            return
        b = InlineKeyboardBuilder()
        for c in cats:
            b.button(text=c.name, callback_data=f"a:products:newcat:{c.id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Vazgeç", callback_data="a:products:list"))
        await safe_edit(cb, "🛍 Ürünün kategorisini seçin:", b.as_markup())
        await cb.answer()
    elif sub == "newcat":
        await state.update_data(new_product={"category_id": int(parts[2])})
        await state.set_state(ProductForm.name)
        await safe_edit(cb, "🛍 Ürün adını yazın:", back_kb("a:products:list"))
        await cb.answer()
    elif sub == "view":
        pid = int(parts[2])
        async with async_session() as s:
            p = await s.get(Product, pid)
            stock_count = (await s.execute(
                select(func.count()).select_from(Stock).where(Stock.product_id == pid, Stock.is_delivered == False)
            )).scalar()
        text = (
            f"🛍 <b>{p.name}</b>\n{p.description}\n\n"
            f"Fiyat: {money(p.price)}"
            + (f" (eski: {money(p.old_price)})" if p.old_price else "") + "\n"
            f"Etiket: {TAGS.get(p.tag,'-')}\n"
            f"Stok: {stock_count}\n"
            f"Aktif: {p.is_active} | Gizli: {p.is_hidden}"
        )
        b = InlineKeyboardBuilder()
        b.button(text="📦 Stok Ekle", callback_data=f"a:stock:add:{pid}")
        b.button(text=("🚫 Pasif Yap" if p.is_active else "✅ Aktif Yap"),
                  callback_data=f"a:products:toggle:{pid}")
        b.button(text=("👁 Göster" if p.is_hidden else "🙈 Gizle"),
                  callback_data=f"a:products:hide:{pid}")
        b.button(text="✏️ Fiyat Değiştir", callback_data=f"a:products:editprice:{pid}")
        b.button(text="✏️ Açıklama Değiştir", callback_data=f"a:products:editdesc:{pid}")
        b.button(text="🗑 Sil", callback_data=f"a:products:delask:{pid}")
        b.adjust(2)
        b.row(InlineKeyboardButton(text="◀️ Liste", callback_data="a:products:list"))
        await safe_edit(cb, text, b.as_markup())
        await cb.answer()
    elif sub == "toggle":
        pid = int(parts[2])
        async with async_session() as s:
            p = await s.get(Product, pid)
            p.is_active = not p.is_active
            await s.commit()
            await log_action(s, cb.from_user.id, "product_toggle", p.name)
        await cb.answer("Güncellendi.")
        await admin_products(cb, state, ["products", "view", str(pid)])
    elif sub == "hide":
        pid = int(parts[2])
        async with async_session() as s:
            p = await s.get(Product, pid)
            p.is_hidden = not p.is_hidden
            await s.commit()
        await cb.answer("Güncellendi.")
        await admin_products(cb, state, ["products", "view", str(pid)])
    elif sub == "editprice":
        await state.set_state(ProductForm.edit_field_value)
        await state.update_data(edit_pid=int(parts[2]), edit_field="price")
        await safe_edit(cb, "💰 Yeni fiyatı yazın:", back_kb(f"a:products:view:{parts[2]}"))
        await cb.answer()
    elif sub == "editdesc":
        await state.set_state(ProductForm.edit_field_value)
        await state.update_data(edit_pid=int(parts[2]), edit_field="description")
        await safe_edit(cb, "📝 Yeni açıklamayı yazın:", back_kb(f"a:products:view:{parts[2]}"))
        await cb.answer()
    elif sub == "delask":
        pid = int(parts[2])
        b = InlineKeyboardBuilder()
        b.button(text="⚠️ Evet, Sil", callback_data=f"a:products:delok:{pid}")
        b.button(text="❌ Vazgeç", callback_data=f"a:products:view:{pid}")
        b.adjust(1)
        await safe_edit(cb, "Bu ürünü ve stoklarını silmek istediğinize emin misiniz?", b.as_markup())
        await cb.answer()
    elif sub == "delok":
        pid = int(parts[2])
        async with async_session() as s:
            p = await s.get(Product, pid)
            name = p.name if p else "?"
            await s.execute(sa_delete(Stock).where(Stock.product_id == pid))
            await s.execute(sa_delete(Product).where(Product.id == pid))
            await s.commit()
            await log_action(s, cb.from_user.id, "product_delete", name)
        await cb.answer("Silindi.")
        await admin_products(cb, state, ["products", "list"])


@admin_router.message(ProductForm.name)
async def msg_product_name(message: Message, state: FSMContext):
    data = await state.get_data()
    data["new_product"]["name"] = message.text.strip()[:160]
    await state.update_data(new_product=data["new_product"])
    await state.set_state(ProductForm.description)
    await message.answer("📝 Ürün açıklamasını yazın:")


@admin_router.message(ProductForm.description)
async def msg_product_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    data["new_product"]["description"] = message.text.strip()
    await state.update_data(new_product=data["new_product"])
    await state.set_state(ProductForm.features)
    await message.answer("✨ Ürün özelliklerini yazın (satır satır olabilir):")


@admin_router.message(ProductForm.features)
async def msg_product_features(message: Message, state: FSMContext):
    data = await state.get_data()
    data["new_product"]["features"] = message.text.strip()
    await state.update_data(new_product=data["new_product"])
    await state.set_state(ProductForm.price)
    await message.answer("💰 Ürün fiyatını yazın (sayı):")


@admin_router.message(ProductForm.price)
async def msg_product_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer("❌ Geçerli bir sayı girin.")
        return
    data = await state.get_data()
    data["new_product"]["price"] = price
    await state.update_data(new_product=data["new_product"])
    await state.set_state(ProductForm.old_price)
    await message.answer("💰 Eski (indirim öncesi) fiyat varsa yazın, yoksa '-' gönderin:")


@admin_router.message(ProductForm.old_price)
async def msg_product_oldprice(message: Message, state: FSMContext):
    data = await state.get_data()
    txt = message.text.strip()
    data["new_product"]["old_price"] = None if txt == "-" else float(txt.replace(",", "."))
    await state.update_data(new_product=data["new_product"])
    await state.set_state(ProductForm.tag)
    b = InlineKeyboardBuilder()
    for k, v in TAGS.items():
        if k != "none":
            b.button(text=v, callback_data=f"tagpick:{k}")
    b.button(text="Etiket Yok", callback_data="tagpick:none")
    b.adjust(2)
    await message.answer("🏷 Ürün etiketini seçin:", reply_markup=b.as_markup())


@admin_router.callback_query(ProductForm.tag, F.data.startswith("tagpick:"))
async def cb_product_tag(cb: CallbackQuery, state: FSMContext):
    tag = cb.data.split(":")[1]
    data = await state.get_data()
    data["new_product"]["tag"] = tag
    await state.update_data(new_product=data["new_product"])
    await state.set_state(ProductForm.images)
    await state.update_data(images=[])
    await cb.message.answer("🖼 Ürün görsellerini gönderin (birden fazla olabilir). Bitince 'bitti' yazın:")
    await cb.answer()


@admin_router.message(ProductForm.images, F.photo)
async def msg_product_image(message: Message, state: FSMContext):
    data = await state.get_data()
    images = data.get("images", [])
    images.append(message.photo[-1].file_id)
    await state.update_data(images=images)
    await message.answer(f"✅ Görsel eklendi ({len(images)}). Bitince 'bitti' yazın.")


@admin_router.message(ProductForm.images, F.text.lower() == "bitti")
async def msg_product_images_done(message: Message, state: FSMContext):
    data = await state.get_data()
    np = data["new_product"]
    async with async_session() as s:
        product = Product(
            category_id=np["category_id"], name=np["name"], description=np.get("description", ""),
            features=np.get("features", ""), price=np["price"], old_price=np.get("old_price"),
            tag=np.get("tag", "none"), images=json.dumps(data.get("images", [])),
        )
        s.add(product)
        await s.commit()
        await log_action(s, message.from_user.id, "product_create", product.name)
    await state.clear()
    await message.answer("✅ Ürün başarıyla oluşturuldu.", reply_markup=admin_menu_kb())


@admin_router.message(ProductForm.edit_field_value)
async def msg_product_edit_field(message: Message, state: FSMContext):
    data = await state.get_data()
    pid, field = data["edit_pid"], data["edit_field"]
    async with async_session() as s:
        p = await s.get(Product, pid)
        old_val = getattr(p, field)
        if field == "price":
            try:
                setattr(p, field, float(message.text.replace(",", ".").strip()))
            except ValueError:
                await message.answer("❌ Geçerli bir sayı girin.")
                return
        else:
            setattr(p, field, message.text.strip())
        await s.commit()
        await log_action(s, message.from_user.id, f"product_edit_{field}", f"{old_val} -> {getattr(p, field)}")
    await state.clear()
    await message.answer("✅ Güncellendi.", reply_markup=admin_menu_kb())


# --- Stok Yönetimi -------------------------------------------------------------
async def admin_stock(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        async with async_session() as s:
            products = (await s.execute(select(Product))).scalars().all()
        b = InlineKeyboardBuilder()
        for p in products:
            b.button(text=p.name, callback_data=f"a:stock:view:{p.id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "📦 <b>Stok Yönetimi</b>\nBir ürün seçin:", b.as_markup())
        await cb.answer()
    elif sub == "view":
        pid = int(parts[2])
        async with async_session() as s:
            p = await s.get(Product, pid)
            available = (await s.execute(
                select(func.count()).select_from(Stock).where(Stock.product_id == pid, Stock.is_delivered == False)
            )).scalar()
            delivered = (await s.execute(
                select(func.count()).select_from(Stock).where(Stock.product_id == pid, Stock.is_delivered == True)
            )).scalar()
        b = InlineKeyboardBuilder()
        b.button(text="➕ Stok Ekle (Toplu)", callback_data=f"a:stock:add:{pid}")
        b.button(text="📤 Mevcut Stoğu Dışa Aktar", callback_data=f"a:stock:export:{pid}")
        b.button(text="🗑 Tüm Stoğu Sil", callback_data=f"a:stock:clearask:{pid}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Liste", callback_data="a:stock:list"))
        await safe_edit(cb, f"📦 <b>{p.name}</b>\nMevcut: {available} | Teslim edilmiş: {delivered}",
                         b.as_markup())
        await cb.answer()
    elif sub == "add":
        await state.set_state(StockForm.bulk_add)
        await state.update_data(stock_pid=int(parts[2]))
        await safe_edit(cb, "📦 Her satıra bir adet olacak şekilde stok içeriklerini (lisans/anahtar/link) yapıştırın:",
                         back_kb(f"a:stock:view:{parts[2]}"))
        await cb.answer()
    elif sub == "export":
        pid = int(parts[2])
        async with async_session() as s:
            rows = (await s.execute(
                select(Stock).where(Stock.product_id == pid, Stock.is_delivered == False)
            )).scalars().all()
        content = "\n".join(r.content for r in rows) or "(boş)"
        doc = BufferedInputFile(content.encode("utf-8"), filename=f"stok_{pid}.txt")
        await cb.message.answer_document(doc, caption="📤 Mevcut stok listesi")
        await cb.answer()
    elif sub == "clearask":
        pid = int(parts[2])
        b = InlineKeyboardBuilder()
        b.button(text="⚠️ Evet, Tümünü Sil", callback_data=f"a:stock:clearok:{pid}")
        b.button(text="❌ Vazgeç", callback_data=f"a:stock:view:{pid}")
        b.adjust(1)
        await safe_edit(cb, "Bu ürüne ait TÜM stok satırlarını (teslim edilenler dahil) silmek istediğinize emin misiniz?",
                         b.as_markup())
        await cb.answer()
    elif sub == "clearok":
        pid = int(parts[2])
        async with async_session() as s:
            await s.execute(sa_delete(Stock).where(Stock.product_id == pid))
            await s.commit()
            await log_action(s, cb.from_user.id, "stock_clear", f"product_id={pid}")
        await cb.answer("Stok temizlendi.")
        await admin_stock(cb, state, ["stock", "view", str(pid)])


@admin_router.message(StockForm.bulk_add)
async def msg_stock_bulk_add(message: Message, state: FSMContext):
    data = await state.get_data()
    pid = data["stock_pid"]
    lines = [l.strip() for l in message.text.splitlines() if l.strip()]
    async with async_session() as s:
        for line in lines:
            s.add(Stock(product_id=pid, content=line))
        await s.commit()
        await log_action(s, message.from_user.id, "stock_add", f"product_id={pid} count={len(lines)}")
    await state.clear()
    await message.answer(f"✅ {len(lines)} adet stok eklendi.", reply_markup=admin_menu_kb())


# --- Sipariş Yönetimi ----------------------------------------------------------
async def admin_orders(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        page = int(parts[2]) if len(parts) > 2 else 0
        async with async_session() as s:
            orders = (await s.execute(
                select(Order).order_by(Order.id.desc()).offset(page * 10).limit(10)
            )).scalars().all()
        b = InlineKeyboardBuilder()
        for o in orders:
            icon = {"completed": "✅", "cancelled": "❌"}.get(o.status, "•")
            b.button(text=f"{icon} #{o.id} — {money(o.total_amount)}", callback_data=f"a:orders:view:{o.id}")
        b.adjust(1)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"a:orders:list:{page-1}"))
        if len(orders) == 10:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"a:orders:list:{page+1}"))
        if nav:
            b.row(*nav)
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "🧾 <b>Siparişler</b>", b.as_markup())
        await cb.answer()
    elif sub == "view":
        oid = int(parts[2])
        async with async_session() as s:
            o = await s.get(Order, oid)
            items = (await s.execute(select(OrderItem).where(OrderItem.order_id == oid))).scalars().all()
        lines = [f"• {it.product_name} — {money(it.price)}" for it in items]
        text = (
            f"🧾 <b>Sipariş #{o.id}</b>\nKullanıcı: <code>{o.user_id}</code>\n"
            f"Durum: {o.status}\nTarih: {o.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"Toplam: {money(o.total_amount)}\n\n" + "\n".join(lines)
        )
        b = InlineKeyboardBuilder()
        if o.status == "completed":
            b.button(text="❌ İptal Et (İade)", callback_data=f"a:orders:cancel:{oid}")
        b.button(text="◀️ Liste", callback_data="a:orders:list:0")
        b.adjust(1)
        await safe_edit(cb, text, b.as_markup())
        await cb.answer()
    elif sub == "cancel":
        oid = int(parts[2])
        async with async_session() as s:
            o = await s.get(Order, oid)
            u = await s.get(User, o.user_id)
            o.status = "cancelled"
            u.balance += o.total_amount
            u.total_spent -= o.total_amount
            await s.commit()
            await log_action(s, cb.from_user.id, "order_cancel", f"order_id={oid}")
        await cb.answer("Sipariş iptal edildi ve bakiye iade edildi.")
        await admin_orders(cb, state, ["orders", "view", str(oid)])


# --- Kullanıcı Yönetimi ---------------------------------------------------------
async def admin_users(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "menu"
    if sub == "menu":
        b = InlineKeyboardBuilder()
        b.button(text="🔍 ID ile Ara", callback_data="a:users:searchid")
        b.button(text="🔍 İsim/Kullanıcı Adı ile Ara", callback_data="a:users:searchname")
        b.button(text="◀️ Admin Panel", callback_data="a:dashboard")
        b.adjust(1)
        await safe_edit(cb, "👥 <b>Kullanıcı Yönetimi</b>", b.as_markup())
        await cb.answer()
    elif sub in ("searchid", "searchname"):
        await state.set_state(SearchForm.user_query)
        await state.update_data(search_mode=sub)
        await safe_edit(cb, "🔍 Aramak istediğiniz değeri yazın:", back_kb("a:users:menu"))
        await cb.answer()
    elif sub == "view":
        uid = int(parts[2])
        async with async_session() as s:
            u = await s.get(User, uid)
        text = (
            f"👤 <b>{u.first_name or ''} {u.last_name or ''}</b>\n"
            f"@{u.username or '-'} | ID: <code>{u.id}</code>\n"
            f"Bakiye: {money(u.balance)}\n"
            f"Toplam sipariş: {u.total_orders} | Harcama: {money(u.total_spent)}\n"
            f"Kayıt: {u.created_at.strftime('%d.%m.%Y')} | Son giriş: {u.last_seen.strftime('%d.%m.%Y %H:%M')}\n"
            f"Durum: {'🚫 Banlı' if u.is_banned else '✅ Aktif'}\n"
            f"Not: {u.note or '-'}"
        )
        b = InlineKeyboardBuilder()
        if u.is_banned:
            b.button(text="✅ Ban Kaldır", callback_data=f"a:users:unban:{uid}")
        else:
            b.button(text="🚫 Banla", callback_data=f"a:users:banask:{uid}")
        b.button(text="📝 Not Ekle", callback_data=f"a:users:noteadd:{uid}")
        b.button(text="💰 Bakiye Ekle/Çıkar", callback_data=f"a:users:balance:{uid}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Geri", callback_data="a:users:menu"))
        await safe_edit(cb, text, b.as_markup())
        await cb.answer()
    elif sub == "banask":
        await state.set_state(UserAdminForm.ban_reason)
        await state.update_data(ban_uid=int(parts[2]))
        await safe_edit(cb, "🚫 Ban sebebini yazın:", back_kb(f"a:users:view:{parts[2]}"))
        await cb.answer()
    elif sub == "unban":
        uid = int(parts[2])
        async with async_session() as s:
            u = await s.get(User, uid)
            u.is_banned = False
            u.ban_reason = None
            await s.commit()
            await log_action(s, cb.from_user.id, "user_unban", str(uid))
        await cb.answer("Ban kaldırıldı.")
        await admin_users(cb, state, ["users", "view", str(uid)])
    elif sub == "noteadd":
        await state.set_state(UserAdminForm.note)
        await state.update_data(note_uid=int(parts[2]))
        await safe_edit(cb, "📝 Not yazın:", back_kb(f"a:users:view:{parts[2]}"))
        await cb.answer()
    elif sub == "balance":
        await state.set_state(UserAdminForm.add_balance)
        await state.update_data(balance_uid=int(parts[2]))
        await safe_edit(cb, "💰 Eklemek (negatif için '-' önekiyle çıkarmak) istediğiniz tutarı yazın:",
                         back_kb(f"a:users:view:{parts[2]}"))
        await cb.answer()


@admin_router.message(SearchForm.user_query)
async def msg_user_search(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("search_mode", "searchid")
    q = message.text.strip()
    async with async_session() as s:
        if mode == "searchid":
            try:
                u = await s.get(User, int(q))
                results = [u] if u else []
            except ValueError:
                results = []
        else:
            results = (await s.execute(
                select(User).where(
                    (User.username.like(f"%{q}%")) | (User.first_name.like(f"%{q}%"))
                ).limit(10)
            )).scalars().all()
    await state.clear()
    if not results:
        await message.answer("❌ Kullanıcı bulunamadı.", reply_markup=admin_menu_kb())
        return
    b = InlineKeyboardBuilder()
    for u in results:
        if u:
            b.button(text=f"{u.first_name or ''} (@{u.username or '-'}) [{u.id}]",
                      callback_data=f"a:users:view:{u.id}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="◀️ Geri", callback_data="a:users:menu"))
    await message.answer("🔍 Sonuçlar:", reply_markup=b.as_markup())


@admin_router.message(UserAdminForm.ban_reason)
async def msg_ban_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = data["ban_uid"]
    async with async_session() as s:
        u = await s.get(User, uid)
        u.is_banned = True
        u.ban_reason = message.text.strip()
        await s.commit()
        await log_action(s, message.from_user.id, "user_ban", f"{uid}: {message.text.strip()}")
    await state.clear()
    await message.answer("✅ Kullanıcı banlandı.", reply_markup=admin_menu_kb())


@admin_router.message(UserAdminForm.note)
async def msg_user_note(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = data["note_uid"]
    async with async_session() as s:
        u = await s.get(User, uid)
        u.note = message.text.strip()
        await s.commit()
    await state.clear()
    await message.answer("✅ Not kaydedildi.", reply_markup=admin_menu_kb())


@admin_router.message(UserAdminForm.add_balance)
async def msg_user_balance_adjust(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = data["balance_uid"]
    try:
        amount = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer("❌ Geçerli bir sayı girin.")
        return
    async with async_session() as s:
        u = await s.get(User, uid)
        u.balance += amount
        await s.commit()
        await log_action(s, message.from_user.id, "user_balance_adjust", f"{uid}: {amount}")
    await state.clear()
    await message.answer(f"✅ Bakiye güncellendi. Yeni bakiye: {money(u.balance)}",
                          reply_markup=admin_menu_kb())


# --- Bakiye Talepleri -----------------------------------------------------------
async def admin_balance_requests(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        async with async_session() as s:
            reqs = (await s.execute(
                select(BalanceRequest).where(BalanceRequest.status == "pending").order_by(BalanceRequest.id)
            )).scalars().all()
        b = InlineKeyboardBuilder()
        for r in reqs:
            b.button(text=f"#{r.id} — {r.user_id} — {money(r.amount)}", callback_data=f"a:balance:view:{r.id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "💳 <b>Bekleyen Bakiye Talepleri</b>", b.as_markup())
        await cb.answer()
    elif sub == "view":
        rid = int(parts[2])
        async with async_session() as s:
            r = await s.get(BalanceRequest, rid)
        b = InlineKeyboardBuilder()
        b.button(text="✅ Onayla", callback_data=f"a:balance:approve:{rid}")
        b.button(text="❌ Reddet", callback_data=f"a:balance:reject:{rid}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Liste", callback_data="a:balance:list"))
        await safe_edit(cb, f"💳 Talep #{r.id}\nKullanıcı: {r.user_id}\nTutar: {money(r.amount)}", b.as_markup())
        await cb.answer()
    elif sub == "approve":
        rid = int(parts[2])
        async with async_session() as s:
            r = await s.get(BalanceRequest, rid)
            u = await s.get(User, r.user_id)
            u.balance += r.amount
            r.status = "approved"
            await s.commit()
            await log_action(s, cb.from_user.id, "balance_approve", f"req={rid}")
        try:
            await cb.bot.send_message(r.user_id, f"✅ Bakiye yükleme talebiniz onaylandı: {money(r.amount)}")
        except Exception:
            pass
        await cb.answer("Onaylandı.")
        await admin_balance_requests(cb, state, ["balance", "list"])
    elif sub == "reject":
        rid = int(parts[2])
        async with async_session() as s:
            r = await s.get(BalanceRequest, rid)
            r.status = "rejected"
            await s.commit()
            await log_action(s, cb.from_user.id, "balance_reject", f"req={rid}")
        try:
            await cb.bot.send_message(r.user_id, f"❌ Bakiye yükleme talebiniz reddedildi.")
        except Exception:
            pass
        await cb.answer("Reddedildi.")
        await admin_balance_requests(cb, state, ["balance", "list"])


# --- Duyuru Yönetimi -------------------------------------------------------------
async def admin_announcements(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        async with async_session() as s:
            anns = (await s.execute(select(Announcement).order_by(Announcement.id.desc()).limit(15))).scalars().all()
        b = InlineKeyboardBuilder()
        for a in anns:
            icon = {"pending": "⏳", "sent": "✅", "cancelled": "❌"}.get(a.status, "•")
            label = a.text[:30].replace("\n", " ")
            b.button(text=f"{icon} {label}", callback_data=f"a:announcements:view:{a.id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="➕ Yeni Duyuru", callback_data="a:announcements:new"))
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "📢 <b>Duyuru Yönetimi</b>", b.as_markup())
        await cb.answer()
    elif sub == "new":
        await state.set_state(AnnouncementForm.text)
        await safe_edit(cb, "📢 Duyuru metnini yazın:", back_kb("a:announcements:list"))
        await cb.answer()
    elif sub == "view":
        aid = int(parts[2])
        async with async_session() as s:
            a = await s.get(Announcement, aid)
        b = InlineKeyboardBuilder()
        if a.status == "pending":
            b.button(text="🚀 Şimdi Gönder", callback_data=f"a:announcements:sendnow:{aid}")
            b.button(text="🗑 İptal Et", callback_data=f"a:announcements:cancel:{aid}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Liste", callback_data="a:announcements:list"))
        sched = a.scheduled_at.strftime('%d.%m.%Y %H:%M') if a.scheduled_at else "—"
        await safe_edit(cb, f"📢 <b>Duyuru #{a.id}</b>\nDurum: {a.status}\nPlanlanan: {sched}\n\n{a.text}",
                         b.as_markup())
        await cb.answer()
    elif sub == "sendnow":
        aid = int(parts[2])
        await send_announcement(cb.bot, aid)
        await cb.answer("Duyuru gönderildi.")
        await admin_announcements(cb, state, ["announcements", "list"])
    elif sub == "cancel":
        aid = int(parts[2])
        async with async_session() as s:
            a = await s.get(Announcement, aid)
            a.status = "cancelled"
            await s.commit()
        await cb.answer("İptal edildi.")
        await admin_announcements(cb, state, ["announcements", "list"])


@admin_router.message(AnnouncementForm.text)
async def msg_announcement_text(message: Message, state: FSMContext):
    await state.update_data(ann_text=message.text)
    await state.set_state(AnnouncementForm.schedule)
    await message.answer(
        "🗓 Hemen göndermek için 'şimdi' yazın, ya da planlamak için 'GG.AA.YYYY SS:DD' formatında tarih yazın:"
    )


@admin_router.message(AnnouncementForm.schedule)
async def msg_announcement_schedule(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data["ann_text"]
    txt = message.text.strip().lower()
    async with async_session() as s:
        if txt == "şimdi" or txt == "simdi":
            a = Announcement(text=text, status="pending", created_by=message.from_user.id)
            s.add(a)
            await s.commit()
            await state.clear()
            await send_announcement(message.bot, a.id)
            await message.answer("✅ Duyuru gönderildi.", reply_markup=admin_menu_kb())
            return
        else:
            try:
                dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
            except ValueError:
                await message.answer("❌ Format hatalı. Örnek: 25.12.2026 18:00")
                return
            a = Announcement(text=text, scheduled_at=dt, status="pending", created_by=message.from_user.id)
            s.add(a)
            await s.commit()
    await state.clear()
    await message.answer("✅ Duyuru planlandı.", reply_markup=admin_menu_kb())


async def send_announcement(bot: Bot, ann_id: int):
    async with async_session() as s:
        a = await s.get(Announcement, ann_id)
        if not a or a.status != "pending":
            return
        users = (await s.execute(select(User.id).where(User.is_banned == False))).scalars().all()
        a.status = "sent"
        a.sent_at = datetime.utcnow()
        await s.commit()
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 <b>Duyuru</b>\n\n{a.text}")
        except Exception:
            pass
        await asyncio.sleep(0.03)  # flood limitine takılmamak için


async def scheduled_announcement_worker(bot: Bot):
    while True:
        try:
            async with async_session() as s:
                due = (await s.execute(
                    select(Announcement).where(
                        Announcement.status == "pending",
                        Announcement.scheduled_at.isnot(None),
                        Announcement.scheduled_at <= datetime.utcnow(),
                    )
                )).scalars().all()
                ids = [a.id for a in due]
            for aid in ids:
                await send_announcement(bot, aid)
        except Exception as e:
            logger.exception("Duyuru zamanlayıcı hatası: %s", e)
        await asyncio.sleep(30)


# --- Destek Yönetimi (admin) ------------------------------------------------------
async def admin_support(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        async with async_session() as s:
            tickets = (await s.execute(
                select(SupportTicket).where(SupportTicket.status == "open")
                .order_by(SupportTicket.last_message_at.desc())
            )).scalars().all()
        b = InlineKeyboardBuilder()
        for t in tickets:
            unread = "🔴" if t.unread_by_admin else "⚪️"
            b.button(text=f"{unread} #{t.id} — {t.subject}", callback_data=f"a:support:view:{t.id}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "💬 <b>Açık Destek Talepleri</b>", b.as_markup())
        await cb.answer()
    elif sub == "view":
        tid = int(parts[2])
        async with async_session() as s:
            t = await s.get(SupportTicket, tid)
            msgs = (await s.execute(
                select(SupportMessage).where(SupportMessage.ticket_id == tid).order_by(SupportMessage.id)
            )).scalars().all()
            t.unread_by_admin = False
            await s.commit()
        lines = [f"{'👤 Kullanıcı' if m.sender=='user' else '🛠 Siz'}: {m.text}" for m in msgs[-15:]]
        text = f"💬 <b>Destek #{t.id}</b> — {t.subject}\nKullanıcı ID: {t.user_id}\n\n" + "\n\n".join(lines)
        b = InlineKeyboardBuilder()
        b.button(text="✉️ Yanıtla", callback_data=f"a:support:reply:{tid}")
        b.button(text="🔒 Kapat", callback_data=f"a:support:close:{tid}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Liste", callback_data="a:support:list"))
        await safe_edit(cb, text, b.as_markup())
        await cb.answer()
    elif sub == "reply":
        tid = int(parts[2])
        await state.set_state(SupportForm.admin_reply)
        await state.update_data(reply_ticket=tid)
        await safe_edit(cb, "✍️ Yanıtınızı yazın:", back_kb(f"a:support:view:{tid}"))
        await cb.answer()
    elif sub == "close":
        tid = int(parts[2])
        async with async_session() as s:
            t = await s.get(SupportTicket, tid)
            t.status = "closed"
            t.closed_at = datetime.utcnow()
            await s.commit()
        try:
            await cb.bot.send_message(t.user_id, f"🔒 Destek talebiniz (#{tid}) yönetici tarafından kapatıldı.")
        except Exception:
            pass
        await cb.answer("Kapatıldı.")
        await admin_support(cb, state, ["support", "list"])


@admin_router.message(SupportForm.admin_reply)
async def msg_admin_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    tid = data["reply_ticket"]
    async with async_session() as s:
        s.add(SupportMessage(ticket_id=tid, sender="admin", text=message.text))
        t = await s.get(SupportTicket, tid)
        t.last_message_at = datetime.utcnow()
        t.unread_by_user = True
        await s.commit()
        user_id = t.user_id
    await state.clear()
    try:
        await message.bot.send_message(
            user_id, f"💬 Destek talebinize yanıt geldi (#{tid}):\n\n{message.text}"
        )
    except Exception:
        pass
    await message.answer("✅ Yanıt gönderildi.", reply_markup=admin_menu_kb())


# --- Loglar ------------------------------------------------------------------
async def admin_logs(cb: CallbackQuery, state: FSMContext, parts):
    page = int(parts[1]) if len(parts) > 1 else 0
    async with async_session() as s:
        logs = (await s.execute(
            select(AdminLog).order_by(AdminLog.id.desc()).offset(page * 10).limit(10)
        )).scalars().all()
    lines = [
        f"🕒 {l.created_at.strftime('%d.%m %H:%M')} | 👤 {l.admin_id} | {l.action} | {l.details}"
        for l in logs
    ]
    text = "🧾 <b>Admin Logları</b>\n\n" + ("\n".join(lines) if lines else "Kayıt yok.")
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"a:logs:{page-1}"))
    if len(logs) == 10:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"a:logs:{page+1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
    await safe_edit(cb, text, b.as_markup())
    await cb.answer()


# --- Yedekleme -----------------------------------------------------------------
async def admin_backup(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "menu"
    if sub == "menu":
        b = InlineKeyboardBuilder()
        b.button(text="💾 Yedek Al ve Gönder", callback_data="a:backup:create")
        b.button(text="◀️ Admin Panel", callback_data="a:dashboard")
        b.adjust(1)
        await safe_edit(cb, "💾 <b>Yedekleme</b>", b.as_markup())
        await cb.answer()
    elif sub == "create":
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.db")
        shutil.copy(DB_PATH, backup_path)
        await cb.message.answer_document(FSInputFile(backup_path), caption="💾 Veritabanı yedeği")
        async with async_session() as s:
            await log_action(s, cb.from_user.id, "backup_create", backup_path)
        await cb.answer("Yedek oluşturuldu.")


# --- Bakım Modu -----------------------------------------------------------------
async def admin_maintenance_toggle(cb: CallbackQuery, state: FSMContext, parts):
    async with async_session() as s:
        cur = await get_setting(s, "maintenance_mode", "0")
        new_val = "0" if cur == "1" else "1"
        await set_setting(s, "maintenance_mode", new_val)
        await log_action(s, cb.from_user.id, "maintenance_toggle", new_val)
    await cb.answer(f"Bakım modu: {'AÇIK' if new_val=='1' else 'KAPALI'}", show_alert=True)
    await admin_dashboard(cb, state, parts)


# --- Yönetici Yönetimi (sadece owner) --------------------------------------------
async def admin_manage_admins(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "list"
    async with async_session() as s:
        me = await is_admin(s, cb.from_user.id)
    if sub == "list":
        async with async_session() as s:
            admins = (await s.execute(select(Admin))).scalars().all()
        b = InlineKeyboardBuilder()
        for a in admins:
            b.button(text=f"👑 {a.user_id} ({a.role})", callback_data=f"a:admins:view:{a.user_id}")
        b.adjust(1)
        if me and me.role == "owner":
            b.row(InlineKeyboardButton(text="➕ Yönetici Ekle", callback_data="a:admins:add"))
        b.row(InlineKeyboardButton(text="◀️ Admin Panel", callback_data="a:dashboard"))
        await safe_edit(cb, "👑 <b>Yöneticiler</b>", b.as_markup())
        await cb.answer()
    elif sub == "add":
        if not me or me.role != "owner":
            await cb.answer("Sadece sahip yönetici ekleyebilir.", show_alert=True)
            return
        await state.set_state(AdminManageForm.add_id)
        await safe_edit(cb, "➕ Eklenecek kullanıcının Telegram ID'sini yazın:", back_kb("a:admins:list"))
        await cb.answer()
    elif sub == "view":
        uid = int(parts[2])
        b = InlineKeyboardBuilder()
        if me and me.role == "owner" and uid not in OWNER_IDS:
            b.button(text="🗑 Yöneticilikten Çıkar", callback_data=f"a:admins:remove:{uid}")
        b.adjust(1)
        b.row(InlineKeyboardButton(text="◀️ Liste", callback_data="a:admins:list"))
        await safe_edit(cb, f"👑 Yönetici ID: <code>{uid}</code>", b.as_markup())
        await cb.answer()
    elif sub == "remove":
        if not me or me.role != "owner":
            await cb.answer("Yetkiniz yok.", show_alert=True)
            return
        uid = int(parts[2])
        async with async_session() as s:
            await s.execute(sa_delete(Admin).where(Admin.user_id == uid))
            await s.commit()
            await log_action(s, cb.from_user.id, "admin_remove", str(uid))
        await cb.answer("Yönetici kaldırıldı.")
        await admin_manage_admins(cb, state, ["admins", "list"])


@admin_router.message(AdminManageForm.add_id)
async def msg_admin_add(message: Message, state: FSMContext):
    async with async_session() as s:
        me = await is_admin(s, message.from_user.id)
        if not me or me.role != "owner":
            await state.clear()
            return
        try:
            new_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Geçerli bir Telegram ID girin.")
            return
        existing = await s.get(Admin, new_id)
        if not existing:
            s.add(Admin(user_id=new_id, role="admin", added_by=message.from_user.id))
            await s.commit()
            await log_action(s, message.from_user.id, "admin_add", str(new_id))
    await state.clear()
    await message.answer("✅ Yönetici eklendi.", reply_markup=admin_menu_kb())


# --- Ayarlar ---------------------------------------------------------------------
async def admin_settings(cb: CallbackQuery, state: FSMContext, parts):
    sub = parts[1] if len(parts) > 1 else "menu"
    if sub == "menu":
        b = InlineKeyboardBuilder()
        b.button(text="✏️ Destek İletişim Metni", callback_data="a:settings:support")
        b.button(text="◀️ Admin Panel", callback_data="a:dashboard")
        b.adjust(1)
        await safe_edit(cb, "⚙️ <b>Ayarlar</b>", b.as_markup())
        await cb.answer()
    elif sub == "support":
        await state.set_state(SettingsForm.support_text)
        await safe_edit(cb, "✏️ Yeni destek iletişim metnini yazın:", back_kb("a:settings:menu"))
        await cb.answer()


@admin_router.message(SettingsForm.support_text)
async def msg_settings_support(message: Message, state: FSMContext):
    async with async_session() as s:
        await set_setting(s, "support_text", message.text.strip())
    await state.clear()
    await message.answer("✅ Güncellendi.", reply_markup=admin_menu_kb())


# ==============================================================================
# WEB İSTATİSTİK PANELİ (aiohttp) — Dark / Cam Efekti / Grafikli
# ==============================================================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Axentra | Yönetim Paneli</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; font-family: 'Segoe UI', system-ui, sans-serif;
    background: radial-gradient(circle at 20% 20%, #0f2027, #0c0c1d 60%);
    color: #e6f1ff; padding: 24px;
  }
  h1 { font-weight: 600; letter-spacing: .5px; margin-bottom: 4px; }
  .sub { color: #7fd8ff; opacity: .7; margin-bottom: 24px; font-size: 14px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .card {
    background: rgba(255,255,255,0.05); border: 1px solid rgba(127,216,255,0.15);
    border-radius: 16px; padding: 18px; backdrop-filter: blur(14px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  }
  .card .label { font-size: 12px; color: #9fb8cc; text-transform: uppercase; letter-spacing: .8px; }
  .card .value { font-size: 26px; font-weight: 700; margin-top: 6px; color: #7fe8ff; }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 800px) { .charts { grid-template-columns: 1fr; } }
  .panel { background: rgba(255,255,255,0.05); border: 1px solid rgba(127,216,255,0.15);
    border-radius: 16px; padding: 18px; backdrop-filter: blur(14px); }
  .panel h3 { margin-top: 0; color: #7fe8ff; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td, th { padding: 6px 4px; border-bottom: 1px solid rgba(255,255,255,0.08); text-align: left; }
</style>
</head>
<body>
  <h1>✨ Axentra Yönetim Paneli</h1>
  <div class="sub">Canlı istatistikler — otomatik yenilenir (30sn)</div>
  <div class="grid" id="statsGrid"></div>
  <div class="charts">
    <div class="panel"><h3>📈 Son 7 Gün Satış</h3><canvas id="salesChart"></canvas></div>
    <div class="panel"><h3>💰 Son 7 Gün Gelir</h3><canvas id="revenueChart"></canvas></div>
  </div>
  <br>
  <div class="panel"><h3>🏆 En Çok Satan Ürünler</h3>
    <table id="topProductsTable"><thead><tr><th>Ürün</th><th>Adet</th></tr></thead><tbody></tbody></table>
  </div>

<script>
const TOKEN = new URLSearchParams(window.location.search).get('token') || '';
let salesChart, revenueChart;

async function loadStats() {
  try {
    const res = await fetch('/api/stats?token=' + encodeURIComponent(TOKEN));
    if (!res.ok) { document.body.innerHTML = '<h2>Erişim reddedildi. Geçersiz token.</h2>'; return; }
    const data = await res.json();
    renderCards(data);
    renderCharts(data);
    renderTopProducts(data);
  } catch (e) { console.error(e); }
}

function renderCards(d) {
  const cards = [
    ['Toplam Kullanıcı', d.total_users], ['Bugünkü Kullanıcı', d.today_users],
    ['Toplam Sipariş', d.total_orders], ['Bugünkü Sipariş', d.today_orders],
    ['Toplam Gelir', d.total_revenue], ['Bugünkü Gelir', d.today_revenue],
    ['Aktif Ürün', d.active_products], ['Pasif Ürün', d.passive_products],
    ['Kategori', d.category_count], ['Açık Destek', d.open_tickets],
  ];
  document.getElementById('statsGrid').innerHTML = cards.map(c =>
    `<div class="card"><div class="label">${c[0]}</div><div class="value">${c[1]}</div></div>`
  ).join('');
}

function renderCharts(d) {
  const ctx1 = document.getElementById('salesChart');
  const ctx2 = document.getElementById('revenueChart');
  if (salesChart) salesChart.destroy();
  if (revenueChart) revenueChart.destroy();
  const opts = { responsive: true, plugins: { legend: { labels: { color: '#e6f1ff' } } },
    scales: { x: { ticks: { color: '#9fb8cc' } }, y: { ticks: { color: '#9fb8cc' } } } };
  salesChart = new Chart(ctx1, { type: 'line', data: { labels: d.days,
    datasets: [{ label: 'Sipariş', data: d.sales_7d, borderColor: '#7fe8ff', backgroundColor: 'rgba(127,232,255,0.15)', fill: true, tension: .35 }] }, options: opts });
  revenueChart = new Chart(ctx2, { type: 'bar', data: { labels: d.days,
    datasets: [{ label: 'Gelir', data: d.revenue_7d, backgroundColor: '#7fd8ff' }] }, options: opts });
}

function renderTopProducts(d) {
  const tbody = document.querySelector('#topProductsTable tbody');
  tbody.innerHTML = d.top_products.map(p => `<tr><td>${p.name}</td><td>${p.count}</td></tr>`).join('');
}

loadStats();
setInterval(loadStats, 30000);
</script>
</body>
</html>
"""


async def web_index(request: web.Request):
    token = request.query.get("token", "")
    if token != WEB_TOKEN:
        return web.Response(text="<h2>Erişim reddedildi.</h2>", content_type="text/html", status=403)
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def web_api_stats(request: web.Request):
    token = request.query.get("token", "")
    if token != WEB_TOKEN:
        return web.json_response({"error": "forbidden"}, status=403)

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    days, sales_7d, revenue_7d = [], [], []
    async with async_session() as s:
        total_users = (await s.execute(select(func.count()).select_from(User))).scalar()
        today_users = (await s.execute(
            select(func.count()).select_from(User).where(User.created_at >= today_start)
        )).scalar()
        total_orders = (await s.execute(select(func.count()).select_from(Order))).scalar()
        today_orders = (await s.execute(
            select(func.count()).select_from(Order).where(Order.created_at >= today_start)
        )).scalar()
        total_revenue = (await s.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.status == "completed")
        )).scalar()
        today_revenue = (await s.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.status == "completed", Order.created_at >= today_start
            )
        )).scalar()
        active_products = (await s.execute(
            select(func.count()).select_from(Product).where(Product.is_active == True)
        )).scalar()
        passive_products = (await s.execute(
            select(func.count()).select_from(Product).where(Product.is_active == False)
        )).scalar()
        category_count = (await s.execute(select(func.count()).select_from(Category))).scalar()
        open_tickets = (await s.execute(
            select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "open")
        )).scalar()

        for i in range(6, -1, -1):
            day = today_start - timedelta(days=i)
            next_day = day + timedelta(days=1)
            days.append(day.strftime("%d.%m"))
            cnt = (await s.execute(
                select(func.count()).select_from(Order).where(
                    Order.created_at >= day, Order.created_at < next_day
                )
            )).scalar()
            rev = (await s.execute(
                select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                    Order.status == "completed", Order.created_at >= day, Order.created_at < next_day
                )
            )).scalar()
            sales_7d.append(cnt)
            revenue_7d.append(round(rev, 2))

        top_rows = (await s.execute(
            select(OrderItem.product_name, func.count().label("cnt"))
            .group_by(OrderItem.product_name).order_by(func.count().desc()).limit(5)
        )).all()
        top_products = [{"name": r[0], "count": r[1]} for r in top_rows]

    return web.json_response({
        "total_users": total_users, "today_users": today_users,
        "total_orders": total_orders, "today_orders": today_orders,
        "total_revenue": round(total_revenue, 2), "today_revenue": round(today_revenue, 2),
        "active_products": active_products, "passive_products": passive_products,
        "category_count": category_count, "open_tickets": open_tickets,
        "days": days, "sales_7d": sales_7d, "revenue_7d": revenue_7d,
        "top_products": top_products,
    })


def build_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", web_index)
    app.router.add_get("/api/stats", web_api_stats)
    return app


# ==============================================================================
# BAŞLATMA (main)
# ==============================================================================
async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(GuardMiddleware())
    dp.include_router(admin_router)
    dp.include_router(user_router)

    tasks = [asyncio.create_task(dp.start_polling(bot))]
    tasks.append(asyncio.create_task(scheduled_announcement_worker(bot)))

    if WEB_ENABLED:
        app = build_web_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
        await site.start()
        logger.info("Web paneli çalışıyor: http://%s:%s/?token=%s", WEB_HOST, WEB_PORT, WEB_TOKEN)

    logger.info("Axentra Seller Bot başlatıldı.")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot durduruldu.")
