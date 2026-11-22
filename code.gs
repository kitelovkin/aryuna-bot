/**
 * Telegram-бот предзаписи Арюны на Google Apps Script (webhook).
 *
 * Листы таблицы:
 *   «Сессии»     — FSM-память (chat_id, state, answers…)
 *   «Расписание» — свободные слоты (A) и статус (B = «Занято»)
 *   «Записи»     — готовые лиды
 *
 * Перед деплоем заполните константы ниже (без реальных секретов в git).
 * После публикации Web App вставьте URL в WEBAPP_URL и запустите setWebhook().
 */

// ——— Константы (плейсхолдеры) ———
var BOT_TOKEN = 'YOUR_BOT_TOKEN';
var ADMIN_ID = 'YOUR_ADMIN_ID';
var WEBAPP_URL = 'YOUR_WEBAPP_URL';

var SHEET_SESSIONS = 'Сессии';
var SHEET_SCHEDULE = 'Расписание';
var SHEET_LEADS = 'Записи';

// Колонки листа «Сессии»: A chat_id, B state, C complaints, D analyses,
// E slot_time, F full_name, G username
var SESSION_COLS = {
  chat_id: 1,
  state: 2,
  complaints: 3,
  analyses: 4,
  slot_time: 5,
  full_name: 6,
  username: 7
};

var START_TEXT =
  'Здравствуйте! Я виртуальный помощник Арюны. Здесь вы можете заполнить ' +
  'анкету предзаписи на нутрициологический разбор питания, анализов и ' +
  'образа жизни.';
var STEP_COMPLAINTS =
  'Кратко опишите, что вас беспокоит (симптомы, лишний вес, проблемы с ЖКТ)?';
var STEP_ANALYSES =
  'Делали ли вы недавно УЗИ брюшной полости или сдавали анализы крови?';
var STEP_TIME = 'Выберите удобное время для онлайн-консультации:';
var STEP_NAME = 'Напишите ваши Фамилию и Имя:';
var STEP_PHONE =
  'Нажмите кнопку ниже, чтобы оставить контактный номер телефона:';
var NO_SLOTS_TEXT = 'К сожалению, сейчас нет свободных окошек.';
var PHONE_BUTTON = '📱 Отправить мой номер';
var CONFIRM_TEXT_PREFIX = '✅ Запись подтверждена. Вы записаны на ';

var ANALYSES_OPTIONS = ['Да, есть свежие', 'Нет', 'Сдавал давно'];

// ——— Telegram helpers ———

/**
 * Отправляет сообщение в Telegram.
 * @param {string|number} chatId
 * @param {string} text
 * @param {Object=} keyboard ReplyKeyboardMarkup или null/undefined
 */
function sendMessage(chatId, text, keyboard) {
  var payload = {
    chat_id: String(chatId),
    text: text
  };
  if (keyboard) {
    payload.reply_markup = JSON.stringify(keyboard);
  }

  var url = 'https://api.telegram.org/bot' + BOT_TOKEN + '/sendMessage';
  UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
}

/** Снимает reply-клавиатуру. */
function removeKeyboard() {
  return {
    remove_keyboard: true
  };
}

/** Reply-клавиатура вариантов про анализы. */
function analysesKeyboard() {
  return {
    keyboard: [
      [{ text: ANALYSES_OPTIONS[0] }],
      [{ text: ANALYSES_OPTIONS[1] }],
      [{ text: ANALYSES_OPTIONS[2] }]
    ],
    resize_keyboard: true,
    one_time_keyboard: true
  };
}

/** Reply-клавиатура слотов: по одной кнопке в ряд. */
function slotsKeyboard(slots) {
  var rows = [];
  for (var i = 0; i < slots.length; i++) {
    rows.push([{ text: String(slots[i]) }]);
  }
  return {
    keyboard: rows,
    resize_keyboard: true,
    one_time_keyboard: true
  };
}

/** Кнопка запроса контакта. */
function phoneKeyboard() {
  return {
    keyboard: [
      [{ text: PHONE_BUTTON, request_contact: true }]
    ],
    resize_keyboard: true,
    one_time_keyboard: true
  };
}

/**
 * Регистрирует webhook Telegram на WEBAPP_URL.
 * Запускайте вручную из редактора Apps Script после деплоя.
 */
function setWebhook() {
  var url =
    'https://api.telegram.org/bot' +
    BOT_TOKEN +
    '/setWebhook?url=' +
    encodeURIComponent(WEBAPP_URL);
  var response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  Logger.log(response.getContentText());
  return response.getContentText();
}

// ——— Лист «Сессии» (FSM) ———

function getSessionsSheet_() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_SESSIONS);
  if (!sheet) {
    throw new Error('Лист «Сессии» не найден');
  }
  return sheet;
}

/**
 * Ищет строку сессии по chat_id. Возвращает объект или null.
 * @param {string|number} chatId
 * @return {Object|null}
 */
function getSession(chatId) {
  var sheet = getSessionsSheet_();
  var data = sheet.getDataRange().getValues();
  var id = String(chatId);

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) === id) {
      return {
        row: i + 1,
        chat_id: data[i][0],
        state: data[i][1] ? String(data[i][1]) : '',
        complaints: data[i][2] ? String(data[i][2]) : '',
        analyses: data[i][3] ? String(data[i][3]) : '',
        slot_time: data[i][4] ? String(data[i][4]) : '',
        full_name: data[i][5] ? String(data[i][5]) : '',
        username: data[i][6] ? String(data[i][6]) : ''
      };
    }
  }
  return null;
}

/**
 * Создаёт или перезаписывает сессию с начальным state.
 * @param {string|number} chatId
 * @param {string} state
 * @param {string=} username
 */
function createSession_(chatId, state, username) {
  clearSession(chatId);
  var sheet = getSessionsSheet_();
  sheet.appendRow([
    String(chatId),
    state,
    '',
    '',
    '',
    '',
    username || ''
  ]);
}

/**
 * Обновляет одно поле сессии (кроме chat_id).
 * @param {string|number} chatId
 * @param {string} key  state|complaints|analyses|slot_time|full_name|username
 * @param {*} value
 */
function updateSession(chatId, key, value) {
  var col = SESSION_COLS[key];
  if (!col) {
    throw new Error('Неизвестное поле сессии: ' + key);
  }
  var session = getSession(chatId);
  if (!session) {
    createSession_(chatId, key === 'state' ? value : '', '');
    session = getSession(chatId);
  }
  getSessionsSheet_().getRange(session.row, col).setValue(value);
}

/**
 * Меняет state сессии.
 * @param {string|number} chatId
 * @param {string} state
 */
function setSessionState(chatId, state) {
  updateSession(chatId, 'state', state);
}

/**
 * Удаляет строку сессии по chat_id.
 * @param {string|number} chatId
 */
function clearSession(chatId) {
  var sheet = getSessionsSheet_();
  var data = sheet.getDataRange().getValues();
  var id = String(chatId);
  for (var i = data.length - 1; i >= 1; i--) {
    if (String(data[i][0]) === id) {
      sheet.deleteRow(i + 1);
    }
  }
}

// ——— Расписание и записи ———

/** Свободные слоты: колонка A, статус B !== «Занято». */
function getFreeSlots() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_SCHEDULE);
  if (!sheet) {
    return [];
  }
  var data = sheet.getDataRange().getValues();
  var free = [];
  for (var i = 1; i < data.length; i++) {
    var slot = data[i][0];
    var status = data[i][1];
    if (slot && String(status) !== 'Занято') {
      free.push(String(slot));
    }
  }
  return free;
}

/** Есть ли слот на листе «Расписание» (любой статус). */
function slotExists(slotTime) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_SCHEDULE);
  if (!sheet) {
    return false;
  }
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (data[i][0] && String(data[i][0]) === String(slotTime)) {
      return true;
    }
  }
  return false;
}

/** Помечает слот «Занято» в колонке B. */
function markSlotTaken(slotTime) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_SCHEDULE);
  if (!sheet) {
    return;
  }
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (data[i][0] && String(data[i][0]) === String(slotTime)) {
      sheet.getRange(i + 1, 2).setValue('Занято');
      break;
    }
  }
}

/**
 * Добавляет лид на лист «Записи».
 * Порядок: Время, ФИО, Телефон, Жалобы, Анализы, Username
 */
function appendLead(slotTime, fullName, phone, complaints, analyses, username) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_LEADS);
  if (!sheet) {
    throw new Error('Лист «Записи» не найден');
  }
  sheet.appendRow([
    slotTime,
    fullName,
    phone,
    complaints,
    analyses,
    username || ''
  ]);
}

function formatUsername_(fromUser) {
  if (fromUser && fromUser.username) {
    return '@' + fromUser.username;
  }
  return 'нет';
}

// ——— Webhook entrypoint ———

/**
 * Telegram webhook: JSON update в e.postData.contents.
 */
function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonOk_();
    }

    var update = JSON.parse(e.postData.contents);
    var message = update.message;
    if (!message || !message.chat) {
      return jsonOk_();
    }

    var chatId = message.chat.id;
    var text = message.text ? String(message.text).trim() : '';
    var contact = message.contact || null;
    var username = formatUsername_(message.from);

    // /start — всегда сбрасывает и начинает анкету с жалоб
    if (text === '/start' || text.indexOf('/start ') === 0) {
      createSession_(chatId, 'COMPLAINTS', username);
      sendMessage(chatId, START_TEXT + '\n\n' + STEP_COMPLAINTS, removeKeyboard());
      return jsonOk_();
    }

    var session = getSession(chatId);
    if (!session || !session.state) {
      sendMessage(chatId, 'Чтобы заполнить анкету, отправьте /start.');
      return jsonOk_();
    }

    var state = session.state;

    if (state === 'COMPLAINTS') {
      handleComplaints_(chatId, text);
    } else if (state === 'ANALYSES') {
      handleAnalyses_(chatId, text);
    } else if (state === 'SLOT_TIME') {
      handleSlotTime_(chatId, text);
    } else if (state === 'FULL_NAME') {
      handleFullName_(chatId, text);
    } else if (state === 'PHONE') {
      handlePhone_(chatId, text, contact, username);
    } else {
      sendMessage(chatId, 'Чтобы заполнить анкету, отправьте /start.');
    }
  } catch (err) {
    Logger.log('doPost error: ' + err);
  }
  return jsonOk_();
}

function jsonOk_() {
  return ContentService.createTextOutput(JSON.stringify({ ok: true })).setMimeType(
    ContentService.MimeType.JSON
  );
}

function handleComplaints_(chatId, text) {
  if (!text || text.charAt(0) === '/') {
    sendMessage(chatId, 'Пожалуйста, опишите жалобы обычным текстовым сообщением.');
    return;
  }
  updateSession(chatId, 'complaints', text);
  setSessionState(chatId, 'ANALYSES');
  sendMessage(chatId, STEP_ANALYSES, analysesKeyboard());
}

function handleAnalyses_(chatId, text) {
  if (ANALYSES_OPTIONS.indexOf(text) === -1) {
    sendMessage(
      chatId,
      'Пожалуйста, выберите один из вариантов на клавиатуре.',
      analysesKeyboard()
    );
    return;
  }
  updateSession(chatId, 'analyses', text);

  var slots = getFreeSlots();
  if (!slots.length) {
    clearSession(chatId);
    sendMessage(chatId, NO_SLOTS_TEXT, removeKeyboard());
    return;
  }

  setSessionState(chatId, 'SLOT_TIME');
  sendMessage(chatId, STEP_TIME, slotsKeyboard(slots));
}

function handleSlotTime_(chatId, text) {
  if (!text || text.charAt(0) === '/') {
    sendMessage(chatId, 'Пожалуйста, выберите время одной из кнопок на клавиатуре.');
    return;
  }
  if (!slotExists(text)) {
    var slots = getFreeSlots();
    if (!slots.length) {
      clearSession(chatId);
      sendMessage(chatId, NO_SLOTS_TEXT, removeKeyboard());
      return;
    }
    sendMessage(
      chatId,
      'Это время недоступно. Пожалуйста, выберите другое удобное время:',
      slotsKeyboard(slots)
    );
    return;
  }
  updateSession(chatId, 'slot_time', text);
  setSessionState(chatId, 'FULL_NAME');
  sendMessage(chatId, STEP_NAME, removeKeyboard());
}

function handleFullName_(chatId, text) {
  if (!text || text.charAt(0) === '/') {
    sendMessage(chatId, 'Пожалуйста, напишите фамилию и имя текстом.');
    return;
  }
  updateSession(chatId, 'full_name', text);
  setSessionState(chatId, 'PHONE');
  sendMessage(chatId, STEP_PHONE, phoneKeyboard());
}

function handlePhone_(chatId, text, contact, username) {
  var phone = '';
  if (contact && contact.phone_number) {
    phone = String(contact.phone_number).trim();
  } else if (text && text !== PHONE_BUTTON && text.charAt(0) !== '/') {
    phone = text;
  }

  if (!phone) {
    sendMessage(
      chatId,
      'Пожалуйста, отправьте номер телефона текстом или нажмите «' +
        PHONE_BUTTON +
        '».',
      phoneKeyboard()
    );
    return;
  }

  var session = getSession(chatId);
  if (
    !session ||
    !session.slot_time ||
    !session.full_name ||
    !session.complaints ||
    !session.analyses
  ) {
    clearSession(chatId);
    sendMessage(
      chatId,
      'Не хватает ответов анкеты. Отправьте /start и заполните её ещё раз.',
      removeKeyboard()
    );
    return;
  }

  var uname = session.username || username || 'нет';
  updateSession(chatId, 'username', uname);

  appendLead(
    session.slot_time,
    session.full_name,
    phone,
    session.complaints,
    session.analyses,
    uname
  );
  markSlotTaken(session.slot_time);

  sendMessage(
    chatId,
    CONFIRM_TEXT_PREFIX +
      session.slot_time +
      '. Врач ознакомится с вашими ответами и свяжется с вами.',
    removeKeyboard()
  );

  if (ADMIN_ID && ADMIN_ID !== 'YOUR_ADMIN_ID') {
    sendMessage(
      ADMIN_ID,
      'Новая запись:\n' +
        '⏱ ' +
        session.slot_time +
        '\n👤 ' +
        session.full_name +
        '\n📞 ' +
        phone +
        '\n💬 ' +
        session.complaints +
        '\n🧪 ' +
        session.analyses +
        '\n' +
        uname
    );
  }

  clearSession(chatId);
}
