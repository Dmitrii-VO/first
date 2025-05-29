import os
import logging
from datetime import datetime
from typing import Dict, List, Optional
import json
import sqlite3

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from flask import Flask, request, jsonify, render_template_string
import asyncio
import threading

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "6712109516:AAHL23ltolowG5kYTfkTKDadg2Io1Rd0WT8"
WEBAPP_URL = "https://gooroo.tools"  # Замените на ваш домен


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('channels.db')
    cursor = conn.cursor()

    # Таблица каналов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_name TEXT NOT NULL,
            channel_username TEXT UNIQUE NOT NULL,
            subscribers_count INTEGER DEFAULT 0,
            category TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица офферов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            advertiser_name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            placement_date DATE,
            rejection_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (channel_id) REFERENCES channels (id)
        )
    ''')

    # Таблица балансов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS balances (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0,
            total_earned REAL DEFAULT 0.0
        )
    ''')

    # Таблица заявок на вывод
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawal_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT NOT NULL,
            payment_details TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


# База данных операции
class Database:
    @staticmethod
    def get_user_channels(user_id: int) -> List[Dict]:
        conn = sqlite3.connect('channels.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM channels WHERE user_id = ?', (user_id,))
        channels = cursor.fetchall()
        conn.close()

        return [
            {
                'id': ch[0], 'user_id': ch[1], 'channel_name': ch[2],
                'channel_username': ch[3], 'subscribers_count': ch[4],
                'category': ch[5], 'description': ch[6]
            }
            for ch in channels
        ]

    @staticmethod
    def add_channel(user_id: int, channel_name: str, channel_username: str,
                    subscribers_count: int, category: str, description: str) -> bool:
        try:
            conn = sqlite3.connect('channels.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO channels (user_id, channel_name, channel_username, 
                                    subscribers_count, category, description)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, channel_name, channel_username, subscribers_count, category, description))
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def get_channel_offers(channel_id: int) -> List[Dict]:
        conn = sqlite3.connect('channels.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM offers WHERE channel_id = ?', (channel_id,))
        offers = cursor.fetchall()
        conn.close()

        return [
            {
                'id': off[0], 'channel_id': off[1], 'title': off[2],
                'description': off[3], 'price': off[4], 'advertiser_name': off[5],
                'status': off[6], 'placement_date': off[7], 'rejection_reason': off[8]
            }
            for off in offers
        ]

    @staticmethod
    def update_offer_status(offer_id: int, status: str, placement_date: str = None,
                            rejection_reason: str = None):
        conn = sqlite3.connect('channels.db')
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE offers SET status = ?, placement_date = ?, rejection_reason = ?
            WHERE id = ?
        ''', (status, placement_date, rejection_reason, offer_id))
        conn.commit()
        conn.close()

    @staticmethod
    def get_user_balance(user_id: int) -> Dict:
        conn = sqlite3.connect('channels.db')
        cursor = conn.cursor()
        cursor.execute('SELECT balance, total_earned FROM balances WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()

        if result:
            return {'balance': result[0], 'total_earned': result[1]}
        return {'balance': 0.0, 'total_earned': 0.0}

    @staticmethod
    def add_withdrawal_request(user_id: int, amount: float, payment_method: str, payment_details: str):
        conn = sqlite3.connect('channels.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO withdrawal_requests (user_id, amount, payment_method, payment_details)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, payment_method, payment_details))
        conn.commit()
        conn.close()


# Flask веб-приложение для Mini App
app = Flask(__name__)

# HTML шаблон для Mini App
WEBAPP_HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gooroo.tools - Панель каналов</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--tg-theme-bg-color, #ffffff);
            color: var(--tg-theme-text-color, #000000);
            padding: 20px;
        }

        .container {
            max-width: 400px;
            margin: 0 auto;
        }

        .header {
            text-align: center;
            margin-bottom: 30px;
        }

        .logo {
            font-size: 24px;
            font-weight: bold;
            color: var(--tg-theme-button-color, #3390ec);
            margin-bottom: 10px;
        }

        .tab-buttons {
            display: flex;
            margin-bottom: 20px;
            border-radius: 10px;
            overflow: hidden;
            background: var(--tg-theme-secondary-bg-color, #f1f1f1);
        }

        .tab-btn {
            flex: 1;
            padding: 12px;
            background: transparent;
            border: none;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }

        .tab-btn.active {
            background: var(--tg-theme-button-color, #3390ec);
            color: white;
        }

        .tab-content {
            display: none;
        }

        .tab-content.active {
            display: block;
        }

        .card {
            background: var(--tg-theme-secondary-bg-color, #f8f9fa);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 16px;
            border: 1px solid var(--tg-theme-section-separator-color, #e0e0e0);
        }

        .btn {
            width: 100%;
            padding: 12px;
            background: var(--tg-theme-button-color, #3390ec);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            cursor: pointer;
            margin-bottom: 10px;
            transition: opacity 0.3s;
        }

        .btn:hover {
            opacity: 0.8;
        }

        .btn-secondary {
            background: var(--tg-theme-secondary-bg-color, #f1f1f1);
            color: var(--tg-theme-text-color, #000);
        }

        .btn-danger {
            background: #ff4757;
        }

        .form-group {
            margin-bottom: 16px;
        }

        .form-label {
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
        }

        .form-input {
            width: 100%;
            padding: 12px;
            border: 1px solid var(--tg-theme-section-separator-color, #e0e0e0);
            border-radius: 8px;
            background: var(--tg-theme-bg-color, white);
            color: var(--tg-theme-text-color, #000);
            font-size: 16px;
        }

        .balance-card {
            text-align: center;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
        }

        .balance-amount {
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 8px;
        }

        .offer-card {
            border-left: 4px solid var(--tg-theme-button-color, #3390ec);
        }

        .offer-title {
            font-weight: bold;
            margin-bottom: 8px;
        }

        .offer-price {
            color: #27ae60;
            font-weight: bold;
            font-size: 18px;
            margin-bottom: 8px;
        }

        .offer-buttons {
            display: flex;
            gap: 8px;
            margin-top: 12px;
        }

        .offer-buttons .btn {
            margin-bottom: 0;
        }

        .status-pending { color: #f39c12; }
        .status-accepted { color: #27ae60; }
        .status-rejected { color: #e74c3c; }

        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 1000;
        }

        .modal-content {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: var(--tg-theme-bg-color, white);
            padding: 20px;
            border-radius: 12px;
            width: 90%;
            max-width: 400px;
        }

        .close {
            float: right;
            font-size: 24px;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">📊 Gooroo.tools</div>
            <p>Панель управления каналами</p>
        </div>

        <div class="tab-buttons">
            <button class="tab-btn active" onclick="showTab('channels')">Каналы</button>
            <button class="tab-btn" onclick="showTab('offers')">Офферы</button>
            <button class="tab-btn" onclick="showTab('balance')">Баланс</button>
        </div>

        <!-- Вкладка каналов -->
        <div id="channels" class="tab-content active">
            <button class="btn" onclick="showAddChannelModal()">➕ Добавить канал</button>
            <div id="channelsList">
                <!-- Каналы будут загружены динамически -->
            </div>
        </div>

        <!-- Вкладка офферов -->
        <div id="offers" class="tab-content">
            <div id="offersList">
                <!-- Офферы будут загружены динамически -->
            </div>
        </div>

        <!-- Вкладка баланса -->
        <div id="balance" class="tab-content">
            <div class="balance-card">
                <div class="balance-amount" id="currentBalance">0 ₽</div>
                <div>Доступно для вывода</div>
            </div>
            <div class="card">
                <div style="margin-bottom: 16px;">
                    <strong>Общий заработок:</strong> <span id="totalEarned">0 ₽</span>
                </div>
                <button class="btn" onclick="showWithdrawModal()">💳 Заявка на вывод</button>
            </div>
        </div>
    </div>

    <!-- Модальное окно добавления канала -->
    <div id="addChannelModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('addChannelModal')">&times;</span>
            <h3>Добавить канал</h3>
            <form id="addChannelForm">
                <div class="form-group">
                    <label class="form-label">Название канала:</label>
                    <input type="text" class="form-input" id="channelName" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Username канала (без @):</label>
                    <input type="text" class="form-input" id="channelUsername" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Количество подписчиков:</label>
                    <input type="number" class="form-input" id="subscribersCount" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Категория:</label>
                    <select class="form-input" id="category" required>
                        <option value="">Выберите категорию</option>
                        <option value="tech">Технологии</option>
                        <option value="business">Бизнес</option>
                        <option value="entertainment">Развлечения</option>
                        <option value="education">Образование</option>
                        <option value="lifestyle">Лайфстайл</option>
                        <option value="news">Новости</option>
                        <option value="crypto">Криптовалюта</option>
                        <option value="other">Другое</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Описание:</label>
                    <textarea class="form-input" id="description" rows="3"></textarea>
                </div>
                <button type="submit" class="btn">Добавить канал</button>
            </form>
        </div>
    </div>

    <!-- Модальное окно вывода средств -->
    <div id="withdrawModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('withdrawModal')">&times;</span>
            <h3>Заявка на вывод средств</h3>
            <form id="withdrawForm">
                <div class="form-group">
                    <label class="form-label">Сумма для вывода:</label>
                    <input type="number" class="form-input" id="withdrawAmount" min="100" required>
                    <small>Минимальная сумма: 100 ₽</small>
                </div>
                <div class="form-group">
                    <label class="form-label">Способ вывода:</label>
                    <select class="form-input" id="paymentMethod" required>
                        <option value="">Выберите способ</option>
                        <option value="card">Банковская карта</option>
                        <option value="qiwi">QIWI кошелек</option>
                        <option value="yoomoney">ЮMoney</option>
                        <option value="crypto">Криптовалюта</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Реквизиты:</label>
                    <input type="text" class="form-input" id="paymentDetails" placeholder="Номер карты/кошелька" required>
                </div>
                <button type="submit" class="btn">Подать заявку</button>
            </form>
        </div>
    </div>

    <!-- Модальное окно принятия оффера -->
    <div id="acceptOfferModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('acceptOfferModal')">&times;</span>
            <h3>Принять оффер</h3>
            <form id="acceptOfferForm">
                <input type="hidden" id="acceptOfferId">
                <div class="form-group">
                    <label class="form-label">Дата размещения:</label>
                    <input type="date" class="form-input" id="placementDate" required>
                </div>
                <button type="submit" class="btn">Принять оффер</button>
            </form>
        </div>
    </div>

    <!-- Модальное окно отклонения оффера -->
    <div id="rejectOfferModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('rejectOfferModal')">&times;</span>
            <h3>Отклонить оффер</h3>
            <form id="rejectOfferForm">
                <input type="hidden" id="rejectOfferId">
                <div class="form-group">
                    <label class="form-label">Причина отказа:</label>
                    <select class="form-input" id="rejectionReason" required>
                        <option value="">Выберите причину</option>
                        <option value="low_price">Низкая цена</option>
                        <option value="inappropriate_content">Неподходящий контент</option>
                        <option value="busy_schedule">Занятое расписание</option>
                        <option value="target_mismatch">Не подходит аудитории</option>
                        <option value="other">Другая причина</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Дополнительный комментарий:</label>
                    <textarea class="form-input" id="rejectionComment" rows="3"></textarea>
                </div>
                <button type="submit" class="btn btn-danger">Отклонить оффер</button>
            </form>
        </div>
    </div>

    <script>
        // Инициализация Telegram Web App
        let tg = window.Telegram.WebApp;
        tg.expand();

        let user_id = tg.initDataUnsafe?.user?.id || 12345; // Для тестирования

        // Навигация по вкладкам
        function showTab(tabName) {
            // Скрыть все вкладки
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('active');
            });

            // Показать выбранную вкладку
            document.getElementById(tabName).classList.add('active');
            event.target.classList.add('active');

            // Загрузить данные для вкладки
            if (tabName === 'channels') loadChannels();
            if (tabName === 'offers') loadOffers();
            if (tabName === 'balance') loadBalance();
        }

        // Управление модальными окнами
        function showModal(modalId) {
            document.getElementById(modalId).style.display = 'block';
        }

        function closeModal(modalId) {
            document.getElementById(modalId).style.display = 'none';
        }

        function showAddChannelModal() {
            showModal('addChannelModal');
        }

        function showWithdrawModal() {
            showModal('withdrawModal');
        }

        function showAcceptOfferModal(offerId) {
            document.getElementById('acceptOfferId').value = offerId;
            showModal('acceptOfferModal');
        }

        function showRejectOfferModal(offerId) {
            document.getElementById('rejectOfferId').value = offerId;
            showModal('rejectOfferModal');
        }

        // API функции
        async function apiCall(endpoint, method = 'GET', data = null) {
            const options = {
                method: method,
                headers: {
                    'Content-Type': 'application/json',
                }
            };

            if (data) {
                options.body = JSON.stringify(data);
            }

            const response = await fetch(`/api${endpoint}`, options);
            return await response.json();
        }

        // Загрузка каналов
        async function loadChannels() {
            try {
                const data = await apiCall(`/channels/${user_id}`);
                const channelsList = document.getElementById('channelsList');

                if (data.channels && data.channels.length > 0) {
                    channelsList.innerHTML = data.channels.map(channel => `
                        <div class="card">
                            <h4>${channel.channel_name}</h4>
                            <p><strong>@${channel.channel_username}</strong></p>
                            <p>👥 ${channel.subscribers_count.toLocaleString()} подписчиков</p>
                            <p>📂 ${channel.category}</p>
                            ${channel.description ? `<p>${channel.description}</p>` : ''}
                        </div>
                    `).join('');
                } else {
                    channelsList.innerHTML = '<div class="card">У вас пока нет зарегистрированных каналов</div>';
                }
            } catch (error) {
                console.error('Ошибка загрузки каналов:', error);
            }
        }

        // Загрузка офферов
        async function loadOffers() {
            try {
                const channelsData = await apiCall(`/channels/${user_id}`);
                let allOffers = [];

                for (const channel of channelsData.channels || []) {
                    const offersData = await apiCall(`/offers/${channel.id}`);
                    allOffers = allOffers.concat(offersData.offers.map(offer => ({
                        ...offer,
                        channel_name: channel.channel_name
                    })));
                }

                const offersList = document.getElementById('offersList');

                if (allOffers.length > 0) {
                    offersList.innerHTML = allOffers.map(offer => `
                        <div class="card offer-card">
                            <div class="offer-title">${offer.title}</div>
                            <div class="offer-price">${offer.price} ₽</div>
                            <p><strong>Канал:</strong> ${offer.channel_name}</p>
                            <p><strong>Рекламодатель:</strong> ${offer.advertiser_name}</p>
                            <p>${offer.description || ''}</p>
                            <p><strong>Статус:</strong> <span class="status-${offer.status}">${getStatusText(offer.status)}</span></p>
                            ${offer.placement_date ? `<p><strong>Дата размещения:</strong> ${offer.placement_date}</p>` : ''}
                            ${offer.rejection_reason ? `<p><strong>Причина отказа:</strong> ${offer.rejection_reason}</p>` : ''}

                            ${offer.status === 'pending' ? `
                                <div class="offer-buttons">
                                    <button class="btn" onclick="showAcceptOfferModal(${offer.id})">✅ Принять</button>
                                    <button class="btn btn-danger" onclick="showRejectOfferModal(${offer.id})">❌ Отклонить</button>
                                </div>
                            ` : ''}
                        </div>
                    `).join('');
                } else {
                    offersList.innerHTML = '<div class="card">У вас пока нет офферов</div>';
                }
            } catch (error) {
                console.error('Ошибка загрузки офферов:', error);
            }
        }

        // Загрузка баланса
        async function loadBalance() {
            try {
                const data = await apiCall(`/balance/${user_id}`);
                document.getElementById('currentBalance').textContent = `${data.balance} ₽`;
                document.getElementById('totalEarned').textContent = `${data.total_earned} ₽`;
            } catch (error) {
                console.error('Ошибка загрузки баланса:', error);
            }
        }

        function getStatusText(status) {
            const statusTexts = {
                'pending': 'Ожидает ответа',
                'accepted': 'Принят',
                'rejected': 'Отклонен'
            };
            return statusTexts[status] || status;
        }

        // Обработчики форм
        document.getElementById('addChannelForm').addEventListener('submit', async function(e) {
            e.preventDefault();

            const formData = {
                user_id: user_id,
                channel_name: document.getElementById('channelName').value,
                channel_username: document.getElementById('channelUsername').value,
                subscribers_count: parseInt(document.getElementById('subscribersCount').value),
                category: document.getElementById('category').value,
                description: document.getElementById('description').value
            };

            try {
                const result = await apiCall('/channels', 'POST', formData);
                if (result.success) {
                    closeModal('addChannelModal');
                    document.getElementById('addChannelForm').reset();
                    loadChannels();
                    tg.showAlert('Канал успешно добавлен!');
                } else {
                    tg.showAlert('Ошибка: ' + result.error);
                }
            } catch (error) {
                tg.showAlert('Ошибка при отправке заявки');
            }
        });

        // Закрытие модальных окон по клику вне области
        window.onclick = function(event) {
            if (event.target.classList.contains('modal')) {
                event.target.style.display = 'none';
            }
        }

        // Инициализация при загрузке
        document.addEventListener('DOMContentLoaded', function() {
            loadChannels();

            // Установка минимальной даты на завтра
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            document.getElementById('placementDate').min = tomorrow.toISOString().split('T')[0];
        });
    </script>
</body>
</html>
'''


# API endpoints
@app.route('/')
def index():
    return render_template_string(WEBAPP_HTML)


@app.route('/api/channels/<int:user_id>')
def get_user_channels(user_id):
    channels = Database.get_user_channels(user_id)
    return jsonify({'channels': channels})


@app.route('/api/channels', methods=['POST'])
def add_channel():
    data = request.get_json()
    success = Database.add_channel(
        data['user_id'],
        data['channel_name'],
        data['channel_username'],
        data['subscribers_count'],
        data['category'],
        data['description']
    )

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Канал с таким username уже существует'})


@app.route('/api/offers/<int:channel_id>')
def get_channel_offers(channel_id):
    offers = Database.get_channel_offers(channel_id)
    return jsonify({'offers': offers})


@app.route('/api/offers/accept', methods=['POST'])
def accept_offer():
    data = request.get_json()
    Database.update_offer_status(
        data['offer_id'],
        'accepted',
        data['placement_date']
    )
    return jsonify({'success': True})


@app.route('/api/offers/reject', methods=['POST'])
def reject_offer():
    data = request.get_json()
    Database.update_offer_status(
        data['offer_id'],
        'rejected',
        rejection_reason=data['rejection_reason']
    )
    return jsonify({'success': True})


@app.route('/api/balance/<int:user_id>')
def get_balance(user_id):
    balance = Database.get_user_balance(user_id)
    return jsonify(balance)


@app.route('/api/withdrawal', methods=['POST'])
def create_withdrawal():
    data = request.get_json()
    Database.add_withdrawal_request(
        data['user_id'],
        data['amount'],
        data['payment_method'],
        data['payment_details']
    )
    return jsonify({'success': True})


# Функция для создания тестовых данных
def create_sample_data():
    """Создает тестовые данные для демонстрации"""
    conn = sqlite3.connect('channels.db')
    cursor = conn.cursor()

    # Добавляем тестовый канал
    cursor.execute('''
        INSERT OR IGNORE INTO channels (user_id, channel_name, channel_username, 
                                      subscribers_count, category, description)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (12345, 'Тестовый канал', 'test_channel', 5000, 'tech', 'Канал о технологиях'))

    # Добавляем тестовые офферы
    cursor.execute('SELECT id FROM channels WHERE channel_username = ?', ('test_channel',))
    channel_result = cursor.fetchone()

    if channel_result:
        channel_id = channel_result[0]
        test_offers = [
            ('Реклама криптобиржи', 'Размещение поста о новой криптобирже', 15000, 'CryptoExchange Ltd', 'pending'),
            (
            'Промо мобильного приложения', 'Пост с обзором мобильного приложения для трейдинга', 8000, 'TradingApp Inc',
            'pending'),
            ('Реклама онлайн-курсов', 'Пост о курсах программирования', 12000, 'EduTech', 'accepted')
        ]

        for title, desc, price, advertiser, status in test_offers:
            cursor.execute('''
                INSERT OR IGNORE INTO offers (channel_id, title, description, price, advertiser_name, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (channel_id, title, desc, price, advertiser, status))

    # Добавляем тестовый баланс
    cursor.execute('''
        INSERT OR REPLACE INTO balances (user_id, balance, total_earned)
        VALUES (?, ?, ?)
    ''', (12345, 25000.0, 45000.0))

    conn.commit()
    conn.close()


# Telegram Bot части
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    keyboard = [
        [InlineKeyboardButton("🚀 Открыть панель управления", web_app=WebAppInfo(url=WEBAPP_URL))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        '🎯 Добро пожаловать в Gooroo.tools!\n\n'
        '📺 Здесь вы можете:\n'
        '• Регистрировать свои каналы\n'
        '• Получать предложения о рекламе\n'
        '• Управлять офферами\n'
        '• Отслеживать баланс и выводить средства\n\n'
        '👇 Нажмите кнопку ниже, чтобы открыть панель управления',
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
🆘 <b>Помощь по использованию Gooroo.tools</b>

<b>📺 Управление каналами:</b>
• Добавляйте свои Telegram каналы в систему
• Указывайте количество подписчиков и категорию
• Система автоматически подберет подходящие офферы

<b>💼 Работа с офферами:</b>
• Получайте предложения о размещении рекламы
• Принимайте офферы с указанием даты размещения
• Отклоняйте неподходящие предложения с указанием причины

<b>💰 Финансы:</b>
• Отслеживайте текущий баланс
• Просматривайте общий заработок
• Подавайте заявки на вывод средств

<b>🔧 Команды бота:</b>
/start - Главное меню
/help - Эта справка
/panel - Открыть панель управления
"""
    await update.message.reply_text(help_text, parse_mode='HTML')


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /panel"""
    keyboard = [
        [InlineKeyboardButton("🚀 Открыть панель", web_app=WebAppInfo(url=WEBAPP_URL))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        '📊 Панель управления каналами',
        reply_markup=reply_markup
    )


def run_flask():
    """Запуск Flask приложения в отдельном потоке"""
    app.run(host='0.0.0.0', port=5000, debug=False)


def main():
    """Основная функция"""
    # Инициализация базы данных
    init_db()

    # Создание тестовых данных (удалите в продакшене)
    create_sample_data()

    # Запуск Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Создание и настройка Telegram бота
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавление обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("panel", panel_command))

    # Запуск бота
    print("🤖 Бот запущен...")
    print("🌐 Flask сервер запущен на порту 5000...")
    application.run_polling()


if __name__ == '__main__':
    main()
    }
    } catch(error)
    {
        tg.showAlert('Ошибка при добавлении канала');
    }
    });

    document.getElementById('acceptOfferForm').addEventListener('submit', async function(e)
    {
        e.preventDefault();

    const
    offerId = document.getElementById('acceptOfferId').value;
    const
    placementDate = document.getElementById('placementDate').value;

try {
const result = await apiCall('/offers/accept', 'POST', {
offer_id: parseInt(offerId),
placement_date: placementDate
});

if (result.success) {
closeModal('acceptOfferModal');
loadOffers();
tg.showAlert('Оффер принят!');
} else {
tg.showAlert('Ошибка: ' + result.error);
}
} catch (error) {
tg.showAlert('Ошибка при принятии оффера');
}
});

document.getElementById('rejectOfferForm').addEventListener('submit', async function(e)
{
e.preventDefault();

const
offerId = document.getElementById('rejectOfferId').value;
const
reason = document.getElementById('rejectionReason').value;
const
comment = document.getElementById('rejectionComment').value;

const
rejectionText = comment ? `${reason}: ${comment}
`: reason;

try {
const result = await apiCall('/offers/reject', 'POST', {
offer_id: parseInt(offerId),
rejection_reason: rejectionText
});

if (result.success) {
closeModal('rejectOfferModal');
loadOffers();
tg.showAlert('Оффер отклонен');
} else {
tg.showAlert('Ошибка: ' + result.error);
}
} catch (error) {
tg.showAlert('Ошибка при отклонении оффера');
}
});

document.getElementById('withdrawForm').addEventListener('submit', async function(e)
{
e.preventDefault();

const
formData = {
    user_id: user_id,
    amount: parseFloat(document.getElementById('withdrawAmount').value),
    payment_method: document.getElementById('paymentMethod').value,
    payment_details: document.getElementById('paymentDetails').value
};

try {
const result = await apiCall('/withdrawal', 'POST', formData);
if (result.success) {
closeModal('withdrawModal');
document.getElementById('withdrawForm').reset();
tg.showAlert('Заявка на вывод отправлена!');
} else {
tg.showAlert('Ошибка: ' + result.error);