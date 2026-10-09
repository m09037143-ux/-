// Правовой Поток — фронтенд, подключённый к реальному backend API.
// Тот же экран/маршруты/CSS, что в исходном прототипе; вся работа с данными
// теперь идёт через fetch() к API вместо localStorage. См. legal-flow/README.md
// (раздел «Frontend») про настройку <meta name="pp-api-base"> и CORS/COOKIE_DOMAIN
// для реального деплоя на отдельном домене.
(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // API-клиент
  // ---------------------------------------------------------------------
  var API_BASE = (document.querySelector('meta[name="pp-api-base"]') || {}).content || '';

  function getCookie(name) {
    var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function ApiError(message, code, status, details) {
    this.message = message; this.code = code; this.status = status; this.details = details || {};
  }
  ApiError.prototype = Object.create(Error.prototype);

  async function api(path, opts) {
    opts = opts || {};
    var method = (opts.method || 'GET').toUpperCase();
    var headers = Object.assign({}, opts.headers || {});
    if (method !== 'GET' && method !== 'HEAD') {
      headers['X-CSRF-Token'] = getCookie('pp_csrf');
    }
    var fetchOpts = { method: method, credentials: 'include', headers: headers };
    if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      fetchOpts.body = JSON.stringify(opts.body);
    }
    var res;
    try {
      res = await fetch(API_BASE + path, fetchOpts);
    } catch (networkErr) {
      throw new ApiError('Нет соединения с сервером API (' + API_BASE + '). Проверьте, что backend запущен.', 'NETWORK_ERROR', 0);
    }
    var text = await res.text();
    var data = null;
    if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
    if (!res.ok) {
      var err = (data && data.error) || {};
      throw new ApiError(err.message || ('Ошибка сервера (' + res.status + ')'), err.code || 'UNKNOWN', res.status, err.details);
    }
    return data;
  }

  // ---------------------------------------------------------------------
  // Локальные демо-фикстуры — используются ТОЛЬКО для гостевого просмотра
  // «пример без регистрации» и никогда не касаются backend.
  // ---------------------------------------------------------------------
  function guestExamples() {
    var heads = [
      ['Ответственность руководителя по долгам компании: что необходимо учитывать', 'garant.ru'],
      ['Взыскание убытков с бывшего директора: обстоятельства спора', 'pravo.ru'],
      ['Оспаривание сделок при банкротстве: основания для проверки', 'garant.ru']
    ];
    return heads.map(function (h, i) {
      return {
        id: 'guest-' + i, title: h[0], discovery_domain: h[1], status: 'DISCOVERED',
        has_official_document: false, created_at: new Date().toISOString(),
        text: 'Демонстрационный пример без подключения к серверу. Зарегистрируйтесь, чтобы работать с реальными данными.'
      };
    });
  }

  // ---------------------------------------------------------------------
  // Состояние приложения
  // ---------------------------------------------------------------------
  var state = {
    route: '/',
    user: null,          // { id, name, email, memberships:[{workspace_id, workspace_name, role}] }
    access: null,         // GET /api/v1/access
    flow: null,           // текущий (единственный в MVP) поток с sources[]
    news: [],
    newsTab: 'Все',
    newsQuery: '',
    newsSourceFilter: 'Все',
    detail: null,         // NewsItemOut текущего открытого материала
    detailTab: 'Текст для публикации',
    editingDraft: false,
    scanJobStatus: null,
    scanJobProgress: 0,
    scanJobProviderName: null,
    exportsList: [],
    publishedCount: 0,
    activityList: [],
    plansList: [],
    ordersList: [],
    adminUsers: null,
    adminForbidden: null, // null = ещё не проверено, true/false = результат последней проверки
    adminTab: 'Пользователи',
    settingsTab: 'Профиль',
    setupStep: 0,
    setupSites: 'garant.ru\npravo.ru',
    setupTheme: 'Банкротство и корпоративные споры',
    setupKeywords: '',
    setupStopWords: '',
    setupSyncedFlowId: null,
    setupRights: false,
    setupPeriod: 'По рабочим дням',
    setupTime: '09:00',
    setupLimit: 3,
    guestNews: guestExamples(),
    guestBrowsing: false,
    booting: true
  };

  var root = document.getElementById('root'), modalRoot = document.getElementById('modal-root');
  var focusBefore = null, confirmFn = null;

  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function value(id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; }

  function toast(msg, isError) {
    var el = document.getElementById('toast-root');
    el.innerHTML = '<div class="toast' + (isError ? ' error' : '') + '">' + esc(msg) + '</div>';
    clearTimeout(toast.t);
    toast.t = setTimeout(function () { el.innerHTML = ''; }, 4200);
  }

  // ---------------------------------------------------------------------
  // Общие строительные блоки разметки (без изменений визуально)
  // ---------------------------------------------------------------------
  var plansStatic = null; // заполняется из GET /api/v1/plans

  function button(label, action, cls, extra) {
    return '<button type="button" class="btn ' + (cls || '') + '" data-action="' + action + '" ' + (extra || '') + '>' + label + '</button>';
  }
  function brand() {
    return '<button class="brand" data-route="/" aria-label="Правовой Поток — на главную"><span class="mark" aria-hidden="true">П</span>Правовой Поток</button>';
  }

  var STATUS_LABELS = {
    DISCOVERED: ['draft', '· Обнаружено'],
    NEEDS_SOURCE: ['draft', '· Нужен официальный документ'],
    NEEDS_REVIEW: ['review', '◉ Требуется проверка'],
    DRAFT: ['draft', '· Черновик'],
    READY_FOR_REVIEW: ['ready', '✓ Готово к утверждению'],
    APPROVED: ['ready', '✓ Утверждено'],
    PUBLISHED: ['published', '✓ Опубликовано'],
    REJECTED: ['rejected', '· Отклонено'],
    SOURCE_POLICY_REVIEW: ['review', '◉ Проверка правил источника'],
    ACCESS_LIMITED: ['rejected', '· Доступ ограничен'],
    AI_ERROR: ['rejected', '· Ошибка ИИ'],
    DUPLICATE: ['draft', '· Дубликат'],
    confirmed: ['confirmed', '✓ Подтверждено'],
    unknown: ['draft', '· Неизвестно'],
    needs_review: ['review', '◉ Требует проверки'],
    contradictory: ['rejected', '· Противоречиво']
  };
  function badge(status) {
    var m = STATUS_LABELS[status] || ['draft', status];
    return '<span class="status ' + m[0] + '">' + esc(m[1]) + '</span>';
  }
  function field(id, label, type, initial) {
    return '<div class="field"><label for="' + id + '">' + label + '</label><input type="' + (type || 'text') + '" id="' + id + '" value="' + esc(initial || '') + '"><span class="error" id="err-' + id + '"></span></div>';
  }

  function textareaField(id, label, initial, hint) {
    return '<div class="field"><label for="' + id + '">' + label + '</label><textarea id="' + id + '" rows="3">' + esc(initial || '') + '</textarea>' + (hint ? '<p class="small">' + hint + '</p>' : '') + '<span class="error" id="err-' + id + '"></span></div>';
  }
  function splitWords(str) {
    return String(str || '').split(/[\n,;]+/).map(function (w) { return w.trim(); }).filter(Boolean);
  }

  var THEME_CATEGORIES = [
    'Уголовные дела экономической направленности',
    'Корпоративные споры',
    'Банкротство',
    'Налоговые споры',
    'Трудовые споры',
    'Интеллектуальная собственность',
    'Антимонопольное регулирование',
    'Административные правонарушения',
    'Земельные и имущественные споры',
    'Семейное право'
  ];

  function go(route, opts) {
    opts = opts || {};
    state.route = route;
    if (!opts.keepEditing) state.editingDraft = false;
    if (route === '/app/admin') state.adminForbidden = null; // не показывать результат прошлой проверки, пока не пришёл новый ответ
    render();
    window.scrollTo(0, 0);
    loadForRoute(route).catch(function (e) {
      if (e.code === 'UNAUTHORIZED') {
        state.user = null; state.access = null;
        toast('Сессия истекла или отсутствует — войдите снова', true);
        state.route = '/login'; render();
      } else {
        toast(e.message, true);
      }
    });
  }

  function banner() {
    if (!state.user) {
      return '<div class="banner"><div><strong>Пример кабинета без регистрации</strong><p>Материалы демонстрационные. Для реальной работы зарегистрируйтесь и включите трёхдневное демо.</p></div>' + button('Зарегистрироваться', 'register', 'primary') + '</div>';
    }
    var a = state.access;
    if (!a) return '';
    if (a.trial_state === 'expired' && !a.can_mutate) {
      return '<div class="banner expired"><div><strong>Демонстрационный доступ завершён</strong><p>Материалы доступны для просмотра, новые действия заблокированы. Списаний нет.</p></div>' + button('Выбрать тариф', 'pricing', 'primary') + '</div>';
    }
    if (a.trial_state === 'expired' && a.can_mutate) {
      // Демо истекло, но доступ есть не по демо — платная подписка или platform_admin
      // (см. trial_service.has_mutating_access): показывать обратный отсчёт демо тут
      // уже не к месту.
      return '<div class="banner"><div><strong>Тариф: ' + esc(a.plan_name || a.plan_code || '—') + '</strong></div></div>';
    }
    var endsAt = new Date(a.trial_ends_at);
    var left = Math.max(0, endsAt - new Date());
    var leftStr = left > 86400000 ? Math.ceil(left / 86400000) + ' дн.' : Math.ceil(left / 3600000) + ' ч.';
    var endStr = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(endsAt) + ' МСК';
    return '<div class="banner"><div><strong>Демо-доступ · осталось ' + leftStr + '</strong><p>До ' + endStr + '. Срок считает сервер, не браузер.</p></div>' + button('Смотреть тарифы', 'pricing') + '</div>';
  }

  function shell(view, isApp) {
    if (isApp) {
      var links = [['/app', 'Обзор'], ['/app/news', 'Новости'], ['/app/sources', 'Источники'], ['/app/schedule', 'Расписание'], ['/app/exports', 'Экспорт'], ['/app/activity', 'Активность'], ['/app/settings', 'Настройки'], ['/app/admin', 'Администрирование']];
      var flowName = state.flow ? state.flow.name : (state.user ? 'Поток не настроен' : 'Пример');
      return '<div class="app"><aside class="sidebar">' + brand() + '<div class="switcher"><span class="small">ТЕКУЩИЙ ПОТОК</span><br><strong>' + esc(flowName) + '</strong></div><nav class="side-nav" aria-label="Кабинет">' + links.map(function (x) { return '<button data-route="' + x[0] + '" class="' + (state.route === x[0] ? 'active' : '') + '">' + x[1] + '</button>'; }).join('') + '</nav><div class="sidebottom"><p class="small">' + (!state.user ? 'Пример кабинета' : (state.access && !state.access.can_mutate ? 'Демо завершено' : 'Тариф: ' + (state.access ? state.access.plan_name : '—'))) + '</p>' + button('Тарифы', 'pricing') + ' ' + button('Выйти', 'logout') + '</div></aside><div style="min-width:0"><header class="topbar row"><strong>Правовой Поток</strong><span class="small">' + esc(state.user ? state.user.name : 'Гость') + '</span></header><main class="content">' + banner() + view + '</main></div></div>';
    }
    return '<div class="public"><header class="pubhead"><div class="wrap row">' + brand() + '<nav class="pubnav" aria-label="Навигация"><button data-route="/features">Возможности</button><button data-route="/pricing">Тарифы</button><button data-route="/faq">FAQ</button><button data-route="/contacts">Контакты</button></nav><div class="row">' + (state.user ? button('В кабинет', 'app', 'primary') : button('Войти', 'login') + button('Зарегистрироваться', 'register', 'primary')) + '</div></div></header><main>' + view + '</main><footer class="pubfoot"><div class="wrap row">' + brand() + '<div class="row">' + button('Тарифы', 'pricing') + button('FAQ', 'faq') + button('Политика', 'privacy') + '</div><small class="small">© 2026 Правовой Поток</small></div></footer></div>';
  }

  function page(h, body, sub, action) {
    return '<div class="pagehead row"><div><h1>' + h + '</h1>' + (sub ? '<p class="muted">' + sub + '</p>' : '') + '</div>' + (action || '') + '</div>' + body;
  }

  // ---------------------------------------------------------------------
  // Публичные страницы
  // ---------------------------------------------------------------------
  function landing() {
    return '<div class="wrap hero"><div><span class="tag">✦ Редакционный сервис юридических новостей</span><h1 style="margin-top:18px">Из правовой новости — <em>в самостоятельный материал для клиентов.</em></h1><p class="muted">Найдите значимую тему, сверьте официальный документ и подготовьте публикацию под контролем юриста.</p><div class="hero-actions">' + button('Зарегистрироваться и попробовать 3 дня', 'register', 'primary') + button('Посмотреть пример без регистрации', 'demo') + '</div><p class="small">72 часа знакомства · без банковской карты.</p></div><div class="mock"><div class="row"><strong>Редакторская карточка</strong><span class="tag">Пример</span></div><div class="mock-row"><strong>Источник обнаружения</strong><p class="small">Остаётся внутри редакции</p></div><div class="mock-row"><strong>Официальный документ</strong><p class="small">Ссылка для проверки и публикации</p></div><div class="mock-row"><strong>Текст для читателя</strong><p class="small">Утверждает юрист, не алгоритм</p></div></div></div><section class="section alt"><div class="wrap"><span class="tag">Ценность</span><h2 style="margin-top:16px">От события до проверенного черновика</h2><div class="grid3"><div class="card"><span class="tag">01 · Новости</span><h3>Общая очередь тем</h3><p>Материалы из выбранных сайтов собраны в одном месте.</p></div><div class="card"><span class="tag">02 · Источники</span><h3>Сверка с документом</h3><p>Новостной сайт отделён от официального первоисточника.</p></div><div class="card"><span class="tag">03 · Редактура</span><h3>Решение за юристом</h3><p>Текст редактируется и утверждается только после проверки.</p></div></div></div></section></div>';
  }
  function features() {
    return '<div class="wrap section"><h1>Возможности</h1><p class="muted">Обнаружение темы → официальный документ → паспорт фактов → новый текст → редакторское утверждение → XML.</p><div class="grid3"><div class="card"><h3>Источник обнаружения</h3><p>Остаётся во внутренней карточке для редактора.</p></div><div class="card"><h3>Проверка</h3><p>Сверка реквизитов и юридически значимых фактов с официальным документом.</p></div><div class="card"><h3>Вывод</h3><p>Самостоятельный текст для читателя и проверенные реквизиты.</p></div></div><p style="margin-top:24px">' + button('Зарегистрироваться', 'register', 'primary') + '</p></div>';
  }
  function pricing() {
    var plans = state.plansList || [];
    return '<div class="wrap section"><h1>Тарифы</h1><p class="muted">Цены за один месяц без автопродления. Приём платежей пока не подключён.</p><div class="grid3">' + plans.map(function (p, i) {
      return '<div class="card plan"><span class="tag">' + (i === 1 ? 'Рекомендуемый' : 'Тариф') + '</span><h2 style="margin:15px 0 5px">' + esc(p.name) + '</h2><div class="price">' + p.price_rub.toLocaleString('ru-RU') + ' ₽ <small>/ месяц</small></div><p>' + planLimitsText(p.limits) + '</p>' + button('Оплатить картой', 'pay', i === 1 ? 'primary' : '', 'data-plan="' + esc(p.code) + '"') + '</div>';
    }).join('') + '</div><p class="notice" style="margin-top:24px">Можно сначала зарегистрироваться и пройти трёхдневное демо без карты. ' + button('Зарегистрироваться', 'register', 'primary') + '</p></div>';
  }
  function planLimitsText(l) {
    if (!l) return '';
    var parts = [];
    if (l.max_flows) parts.push('до ' + l.max_flows + ' поток(ов)');
    if (l.max_sources_per_flow) parts.push('до ' + l.max_sources_per_flow + ' сайтов');
    if (l.max_news_per_run) parts.push('до ' + l.max_news_per_run + ' новостей за запуск');
    if (l.manual_or_daily_schedule_only) parts.push('ручной/ежедневный запуск');
    return parts.join(' · ');
  }
  function faq() {
    var q = [
      ['Как зарегистрироваться?', 'Нажмите «Зарегистрироваться» в шапке и укажите имя, email и пароль.'],
      ['Как работает три дня?', '72 часа отсчитывает сервер с момента регистрации; сброс браузера или повторный вход срок не продлевают и не сбрасывают.'],
      ['Откуда берутся новости?', 'С сайтов, которые вы добавляете в поток: система проверяет robots.txt, находит ленту (RSS/Atom, sitemap или список статей) и собирает материалы по расписанию. Публикации автоматом нет — каждый материал проверяет редактор. В демо-режиме сервера вместо этого показываются демонстрационные материалы (DEMO_FIXTURE).'],
      ['Можно оплатить картой?', 'Пока нет — платёжный провайдер не подключён, кнопка оплаты вернёт понятное сообщение об этом.']
    ];
    return '<div class="wrap section" style="max-width:850px"><h1>FAQ</h1>' + q.map(function (x) { return '<details class="card" style="margin:10px 0"><summary style="cursor:pointer;font-weight:700">' + x[0] + '</summary><p style="margin:13px 0 0">' + x[1] + '</p></details>'; }).join('') + '</div>';
  }
  function contacts() {
    return '<div class="wrap section" style="max-width:760px"><h1>Контакты</h1><p class="notice">Форма демонстрационная; сообщения не отправляются (почтовый сервис не подключён).</p><form id="contact-form" class="card">' + field('contact-name', 'Имя') + field('contact-email', 'Email', 'email') + '<div class="field"><label for="contact-text">Сообщение</label><textarea id="contact-text"></textarea></div><button class="btn primary">Проверить форму</button></form></div>';
  }
  function privacy() {
    return '<div class="wrap section" style="max-width:850px"><h1>Информация о демонстрации</h1><p class="notice">Не является готовой политикой обработки персональных данных.</p><h2>Регистрация</h2><p>Пароль хранится на сервере только в виде Argon2id-хеша.</p><h2>Трёхдневный срок</h2><p>Считается и хранится на сервере, не в браузере.</p><h2>Платежи и сбор</h2><p>Карты не принимаются, реальный сбор с сайтов не подключён.</p></div>';
  }
  function auth(kind) {
    return '<div class="auth"><aside class="authside"><span class="tag">Правовой Поток</span><h1>Новости — в работе. Контроль — у юриста.</h1><p class="muted">Зарегистрируйтесь для знакомства с интерфейсом на 72 часа.</p></aside><div class="authmain"><form id="auth-form" data-kind="' + kind + '" class="card"><h2>' + (kind === 'register' ? 'Регистрация' : kind === 'forgot' ? 'Восстановление доступа' : 'Вход') + '</h2>' +
      (kind === 'register' ? '<p>После регистрации откроется трёхдневный демонстрационный кабинет.</p>' + field('auth-name', 'Имя') : '') +
      field('auth-email', 'Рабочий email', 'email') +
      (kind === 'forgot' ? '' : field('auth-password', kind === 'register' ? 'Придумайте пароль' : 'Пароль', 'password')) +
      (kind === 'register' ? field('auth-repeat', 'Повторите пароль', 'password') + '<label><input type="checkbox" id="auth-ok"> Ознакомлен с <button type="button" class="link" data-route="/privacy">информацией о демонстрации</button></label>' : '') +
      '<p><button class="btn primary" style="width:100%;margin-top:14px">' + (kind === 'register' ? 'Зарегистрироваться и начать демо' : kind === 'forgot' ? 'Продолжить' : 'Войти') + '</button></p>' +
      (kind === 'login' ? '<p><strong>Нет аккаунта?</strong> ' + button('Зарегистрироваться', 'register', 'secondary') + '</p>' + button('Посмотреть пример без регистрации', 'demo') + ' ' + button('Забыли пароль?', 'forgot') : '<p>' + button('Уже зарегистрировались? Войти', 'login') + '</p>') +
      '<div id="auth-extra"></div>' +
      '</form></div></div>';
  }

  // ---------------------------------------------------------------------
  // Кабинет
  // ---------------------------------------------------------------------
  function progressCard() {
    var label = state.scanJobStatus === 'running' ? 'Обработка запущенного задания…'
      : state.scanJobStatus === 'pending' ? 'Задание в очереди…'
      : state.scanJobStatus === 'done' ? 'Последний запуск завершён'
      : state.scanJobStatus === 'failed' ? 'Последний запуск завершился с ошибкой'
      : 'Ожидает запуска';
    var providerNote = state.scanJobProviderName === 'fixture'
      ? 'Последний запуск: провайдер DEMO_FIXTURE — реальные сайты не анализировались.'
      : state.scanJobProviderName === 'html'
        ? 'Последний запуск: реальный сбор с сайтов потока.'
        : 'Сбор идёт по ленте каждого сайта потока (RSS, sitemap или список статей); демонстрационные материалы DEMO_FIXTURE — только в демо-режиме сервера.';
    return '<div class="card"><h3>Фоновая задача сбора</h3><p>' + label + '</p><div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + state.scanJobProgress + '"><span style="width:' + state.scanJobProgress + '%"></span></div><p class="small" style="margin-top:15px">' + providerNote + '</p>' + button(state.flow ? 'Настроить поток' : 'Создать поток', 'setup') + '</div>';
  }

  function currentNewsSource() { return state.user ? state.news : state.guestNews; }

  function dashboard() {
    var news = currentNewsSource();
    var kpiTime = state.flow ? state.flow.schedule_time : '—';
    var kpiPeriod = state.flow ? state.flow.schedule_period : 'Поток не создан';
    return page('Добрый день' + (state.user ? ', ' + esc(state.user.name) : ''),
      '<div class="card"><h3>Начните с примера</h3><p>Откройте материал → изучите внутренний источник → укажите официальный документ → сохраните текст → проверьте факты → утвердите.</p>' + (news[0] ? button('Открыть первый материал', 'open', 'secondary', 'data-id="' + news[0].id + '"') : '<p class="small">Материалов пока нет — запустите сбор.</p>') + '</div>' +
      '<div class="kpis"><div class="card"><span class="small">План запуска</span><strong>' + esc(kpiTime) + '</strong><span class="small">' + esc(kpiPeriod) + '</span></div><div class="card"><span class="small">Материалов</span><strong>' + news.length + '</strong></div><div class="card"><span class="small">Требуют проверки</span><strong>' + news.filter(function (x) { return x.status === 'NEEDS_REVIEW' || x.status === 'DISCOVERED'; }).length + '</strong></div><div class="card"><span class="small">Опубликовано</span><strong>' + news.filter(function (x) { return x.status === 'PUBLISHED'; }).length + '</strong></div></div>' +
      '<div class="dashboard"><div class="card"><div class="row"><h3>Последние материалы</h3>' + button('Все новости →', 'news') + '</div>' + news.slice(0, 4).map(function (x) { return '<div class="news-row"><strong>' + esc(x.title) + '</strong><br><span class="small">Сайт обнаружения: ' + esc(x.discovery_domain) + '</span> ' + badge(x.status) + ' ' + button('Открыть', 'open', '', 'data-id="' + x.id + '"') + '</div>'; }).join('') + '</div>' + progressCard() + '</div>',
      state.flow ? 'Поток: ' + esc(state.flow.theme) : 'Поток ещё не настроен',
      state.flow ? button('▶ Приступить к сбору', 'collect', 'primary') : '');
  }

  function allowedSchedulePeriods() {
    var limits = state.access && state.access.limits;
    if (limits && limits.manual_or_daily_schedule_only) return ['Вручную', 'Ежедневно'];
    return ['Вручную', 'Ежедневно', 'По рабочим дням', 'Раз в неделю'];
  }

  function setupPage() {
    var i = state.setupStep, hasFlow = !!state.flow;
    if (hasFlow && state.setupSyncedFlowId !== state.flow.id) {
      state.setupSyncedFlowId = state.flow.id;
      state.setupTheme = state.flow.theme;
      state.setupKeywords = (state.flow.keywords || []).join('\n');
      state.setupStopWords = (state.flow.stop_words || []).join('\n');
      state.setupPeriod = state.flow.schedule_period; state.setupTime = state.flow.schedule_time; state.setupLimit = state.flow.news_limit_per_run;
    }
    var allowedPeriods = allowedSchedulePeriods();
    if (allowedPeriods.indexOf(state.setupPeriod) === -1) state.setupPeriod = allowedPeriods[0];
    var body;
    if (i === 0) {
      body = '<h3>Сайты</h3>' + (hasFlow
        ? '<p class="notice">Сайты после создания потока управляются на странице «Источники». Текущие: ' + esc((state.flow.sources || []).map(function (s) { return s.domain; }).join(', ') || '—') + '</p>'
        : '<div class="field"><label for="setup-sites">Домены по одному на строке</label><textarea id="setup-sites">' + esc(state.setupSites) + '</textarea><span class="error" id="err-setup-sites"></span></div>'
          + '<label class="small" style="display:flex;gap:9px;align-items:flex-start"><input type="checkbox" id="setup-rights" style="margin-top:3px" ' + (state.setupRights ? 'checked' : '') + '><span>Для сайтов, кроме garant.ru, pravo.ru и consultant.ru: подтверждаю, что вправе использовать их материалы. Система проверит robots.txt и сама найдёт ленту новостей.</span></label>');
    } else if (i === 1) {
      body = '<h3>Тематика</h3><div class="field"><label for="setup-category">Готовая категория (необязательно)</label>'
        + '<select id="setup-category"><option value="">— выбрать из списка —</option>'
        + THEME_CATEGORIES.map(function (c) { return '<option ' + (c === state.setupTheme ? 'selected' : '') + '>' + esc(c) + '</option>'; }).join('')
        + '</select></div>'
        + field('setup-theme', 'Тема потока', 'text', state.setupTheme)
        + textareaField('setup-keywords', 'Ключевые слова (необязательно)', state.setupKeywords, 'По одному на строке или через запятую. В материале должно встретиться хотя бы одно слово. Работает по основе слова: «банкротство» найдёт и «банкротстве».')
        + textareaField('setup-stopwords', 'Стоп-слова (необязательно)', state.setupStopWords, 'Материал, где встретится любое из этих слов, будет пропущен.');
    } else if (i === 2) {
      body = '<h3>Расписание</h3><div class="field"><label for="setup-period">Периодичность</label><select id="setup-period">' + allowedPeriods.map(function (v) { return '<option ' + (v === state.setupPeriod ? 'selected' : '') + '>' + v + '</option>'; }).join('') + '</select>' + (allowedPeriods.length < 4 ? '<p class="small">Текущий тариф допускает только ручной или ежедневный запуск.</p>' : '') + '</div>' + field('setup-time', 'Время, Москва UTC+3', 'time', state.setupTime);
    } else {
      body = '<h3>Количество новостей за запуск</h3>' + [1, 3, 5, 10].map(function (v) { return '<label style="display:inline-block;margin:9px"><input type="radio" name="limit" value="' + v + '" ' + (state.setupLimit === v ? 'checked' : '') + '> ' + v + '</label>'; }).join('') + '<p class="notice">' + esc(state.setupTheme) + ' · ' + esc(state.setupPeriod) + ', ' + esc(state.setupTime) + (splitWords(state.setupKeywords).length ? '<br>Ключевые слова: ' + esc(splitWords(state.setupKeywords).join(', ')) : '') + (splitWords(state.setupStopWords).length ? '<br>Стоп-слова: ' + esc(splitWords(state.setupStopWords).join(', ')) : '') + '</p>';
    }
    return page(hasFlow ? 'Настроить поток' : 'Создать поток', '<p class="tag">Шаг ' + (i + 1) + ' из 4</p><div class="card" style="max-width:740px">' + body + '<div class="row" style="margin-top:22px">' + (i ? button('Назад', 'setupBack') : '<span></span>') + button(i === 3 ? 'Сохранить' : 'Продолжить', i === 3 ? 'setupSave' : 'setupNext', 'primary') + '</div></div>', 'Лимиты (число потоков/сайтов/новостей) проверяет сервер по вашему тарифу.');
  }

  var TAB_TO_STATUS = { 'Все': null, 'Требуют проверки': 'NEEDS_REVIEW', 'Готовы': 'READY_FOR_REVIEW', 'Опубликованные': 'PUBLISHED', 'Отклонённые': 'REJECTED' };

  function newsRowsHtml() {
    var list = currentNewsSource().filter(function (x) {
      var st = TAB_TO_STATUS[state.newsTab];
      return (!st || x.status === st)
        && x.title.toLowerCase().indexOf(state.newsQuery.toLowerCase()) !== -1
        && (state.newsSourceFilter === 'Все' || x.discovery_domain === state.newsSourceFilter);
    });
    return list.length ? '<table class="table"><thead><tr><th>Материал</th><th>Источник обнаружения</th><th>Официальный документ</th><th>Статус</th><th>Действие</th></tr></thead><tbody>' + list.map(function (x) {
      return '<tr><td>' + esc(x.title) + '</td><td>' + esc(x.discovery_domain) + '</td><td>' + (x.has_official_document ? 'Указан' : 'Не указан') + '</td><td>' + badge(x.status) + '</td><td>' + button('Открыть', 'open', '', 'data-id="' + x.id + '"') + '</td></tr>';
    }).join('') + '</tbody></table>' : '<p>По выбранным фильтрам материалов нет.</p>';
  }

  function newsPage() {
    var domains = state.flow ? (state.flow.sources || []).map(function (s) { return s.domain; }) : Array.from(new Set(state.guestNews.map(function (x) { return x.discovery_domain; })));
    return page('Новости', '<div class="filters"><input class="filter" id="search" placeholder="Поиск по заголовку" aria-label="Поиск" value="' + esc(state.newsQuery) + '"><select class="filter" id="source-filter" aria-label="Сайт"><option>Все</option>' + domains.map(function (d) { return '<option ' + (d === state.newsSourceFilter ? 'selected' : '') + '>' + esc(d) + '</option>'; }).join('') + '</select><span></span></div><div class="tabs">' + Object.keys(TAB_TO_STATUS).map(function (v) { return '<button data-tab="' + v + '" class="' + (state.newsTab === v ? 'active' : '') + '">' + v + '</button>'; }).join('') + '</div><div id="news-results" class="card table-scroll">' + newsRowsHtml() + '</div>' + (state.flow ? progressCard() : ''), 'Материалы и редакционные статусы', state.flow ? button('▶ Приступить к сбору', 'collect', 'primary') : '');
  }

  function officialBlock(doc) {
    if (doc && doc.url) {
      return '<div class="source-box"><strong>Официальный первоисточник</strong><p>' + esc(doc.title) + '</p><a href="' + esc(doc.url) + '" target="_blank" rel="noopener noreferrer">' + esc(doc.url) + '</a><p class="small">' + esc(doc.requisites || '') + '</p><p class="small">' + (doc.checked_at ? 'Проверен редактором ' + new Date(doc.checked_at).toLocaleString('ru-RU') : 'Ещё не подтверждён редактором') + '</p></div>';
    }
    return '<p class="notice warning">Официальный документ не указан — утверждение недоступно.</p>';
  }

  function detailPage() {
    var d = state.detail;
    if (!d) return page('Материал не найден', button('Назад', 'news'));
    var tab = state.detailTab, main;
    if (tab === 'Текст для публикации') {
      main = state.editingDraft
        ? field('edit-title', 'Заголовок', 'text', d.title) + '<div class="field"><label for="edit-text">Самостоятельный текст</label><textarea id="edit-text" style="min-height:290px">' + esc(d.text) + '</textarea></div><p class="small" id="counter">Символов: ' + d.text.length + '</p>'
          + button('Сгенерировать черновик через ИИ (YandexGPT Pro 5.1)', 'aiDraft', 'secondary')
        : '<article class="article"><h2>' + esc(d.title) + '</h2>' + esc(d.text) + '</article>';
    } else if (tab === 'Источник обнаружения') {
      main = '<p class="notice warning">Внутренний раздел. Текст новостного сайта не переносится в публикацию.</p><div class="source-box"><strong>' + esc(d.discovery_domain) + '</strong><p>' + esc(d.discovery_original_fragment) + '</p></div>';
    } else if (tab === 'Официальные источники') {
      main = officialBlock(d.official_document) + (state.access && state.access.can_mutate
        ? field('official-title', 'Название и реквизиты документа', 'text', d.official_document ? d.official_document.title : '') + field('official-url', 'Ссылка на официальный документ', 'url', d.official_document ? d.official_document.url : '') + field('official-requisites', 'Реквизиты (дело, дата)', 'text', d.official_document ? d.official_document.requisites : '') + button('Сохранить документ', 'saveOfficial')
        : '');
    } else if (tab === 'Паспорт фактов') {
      main = '<div class="source-box"><strong>Юридически значимые сведения</strong><p style="white-space:pre-wrap">' + esc(d.fact_passport ? d.fact_passport.text : '') + '</p>' + (d.fact_passport ? badge(d.fact_passport.overall_status) : '') + '</div>' + (state.access && state.access.can_mutate
        ? '<div class="field"><label for="facts-edit">Отредактировать паспорт по официальному документу</label><textarea id="facts-edit">' + esc(d.fact_passport ? d.fact_passport.text : '') + '</textarea></div><div class="field"><label for="facts-status">Статус</label><select id="facts-status">' + ['confirmed', 'unknown', 'needs_review', 'contradictory'].map(function (s) { return '<option value="' + s + '" ' + (d.fact_passport && d.fact_passport.overall_status === s ? 'selected' : '') + '>' + s + '</option>'; }).join('') + '</select></div>' + button('Сохранить паспорт', 'saveFacts')
        : '') + '<p class="notice warning" style="margin-top:12px">Ни одна дата или реквизит не проверяются программой автоматически.</p>';
    } else if (tab === 'Предпросмотр для читателя') {
      main = '<div id="reader-slot"><p class="small">Загрузка публичного предпросмотра…</p></div>';
    } else {
      main = '<div id="history-slot"><p class="small">Загрузка истории…</p></div>';
    }
    return page(esc(d.title), '<p>' + button('← Новости', 'news') + ' · ' + badge(d.status) + '</p><div class="detail"><div class="card"><div class="tabs">' + ['Текст для публикации', 'Источник обнаружения', 'Официальные источники', 'Паспорт фактов', 'Предпросмотр для читателя', 'История'].map(function (v) { return '<button data-detail="' + v + '" class="' + (v === tab ? 'active' : '') + '">' + v + '</button>'; }).join('') + '</div>' + main + '</div><aside class="card"><h3>Редакторский контроль</h3><p>' + badge(d.status) + '</p><p class="small">Сайт обнаружения: ' + esc(d.discovery_domain) + ' — только редактору</p><p class="small">Официальный документ: ' + (d.official_document && d.official_document.url ? 'указан' : 'не указан') + '</p><p class="small">Реквизиты проверены: ' + (d.official_reviewed ? 'да' : 'нет') + '</p><p class="small">Факты проверены: ' + (d.facts_reviewed ? 'да' : 'нет') + '</p><p class="notice warning">Все решения — только вручную, автопубликации нет.</p><div style="display:grid;gap:9px">' + button(state.editingDraft ? 'Сохранить черновик' : 'Редактировать', state.editingDraft ? 'saveDraft' : 'edit') + (d.status === 'PUBLISHED' ? '' : button('Проверка редактором', 'review', 'secondary') + button('Утвердить материал', 'approve', 'primary') + (d.status === 'APPROVED' ? button('Опубликовать', 'publish', 'primary') : '') + button('Отклонить', 'reject')) + '</div></aside></div>');
  }

  var SOURCE_KIND_LABELS = { builtin: 'готовый разбор сайта', feed: 'RSS/Atom-лента', sitemap: 'новостной sitemap', html: 'список статей (эвристика)' };

  function sourcesPage() {
    if (!state.flow) return page('Сайты для обнаружения тем', '<p class="notice">Сначала создайте поток.</p>', '', button('Создать поток', 'setup', 'primary'));
    var sources = state.flow.sources || [];
    var notice = state.flow.real_collection_enabled ? ''
      : '<p class="notice warning">Сейчас включён демонстрационный режим: сбор идёт по демо-материалам (DEMO_FIXTURE), реальные сайты не анализируются. Реальный сбор включает администратор сервера.</p>';
    return page('Сайты для обнаружения тем', notice + '<div style="display:grid;gap:12px">' + sources.map(function (x) {
      var ready = x.status === 'ready';
      var badge = ready
        ? '<span class="status ready">Сбор: ' + esc(SOURCE_KIND_LABELS[x.kind] || 'настроен') + '</span>'
        : '<span class="status draft">Нет способа сбора</span>';
      var note = ready ? (x.status_note || 'Право использования подтверждено, robots.txt соблюдается.') : (x.status_note || 'Способ сбора ещё не определён — проверим при следующем сборе.');
      return '<div class="card row"><div><strong>' + esc(x.domain) + '</strong> ' + badge + '<p class="small">' + esc(note) + '</p></div><div class="row"><label><input type="checkbox" data-toggle="' + x.id + '" ' + (x.active ? 'checked' : '') + '> Активен</label>' + button('Изменить', 'editSource', '', 'data-id="' + x.id + '"') + button('Удалить', 'deleteSource', '', 'data-id="' + x.id + '"') + '</div></div>';
    }).join('') + '</div>', 'Добавьте любой новостной сайт: система проверит robots.txt, найдёт ленту и будет собирать материалы сама. Сайты обнаружения не являются официальными документами.', button('Добавить сайт', 'addSource'));
  }

  function schedulePage() {
    if (!state.flow) return page('Расписание', '<p class="notice">Сначала создайте поток.</p>', '', button('Создать поток', 'setup', 'primary'));
    return page('Расписание', '<div class="grid2"><div class="card"><h3>План запуска</h3><p>' + esc(state.flow.schedule_period) + ', ' + esc(state.flow.schedule_time) + ' · Москва UTC+3</p><p>До ' + state.flow.news_limit_per_run + ' новостей за запуск</p>' + button('Изменить', 'changeSchedule') + '</div><div class="card"><h3>Ручной запуск</h3><p>Тема: ' + esc(state.flow.theme) + '</p>' + button('▶ Приступить к сбору', 'collect', 'primary') + '</div></div>', 'Расписание в MVP запускается только вручную кнопкой — планировщик по времени не подключён (см. ROADMAP).');
  }

  function exportsPage() {
    var published = state.user ? (state.publishedCount || 0) : currentNewsSource().filter(function (x) { return x.status === 'PUBLISHED'; }).length;
    return page('Экспорт', '<div class="grid2"><div class="card"><h3>Яндекс.Диск</h3><p class="notice">Не подключён. Реальной отправки нет.</p></div><div class="card"><h3>XML для сайта</h3><p>Опубликованных материалов: ' + published + '</p><p class="small">В XML включены только текст и официальный документ; сайт обнаружения не входит.</p>' + field('xml-name', 'Название файла', 'text', 'legal-news.xml') + button('Предпросмотр / Сформировать XML', 'exportXml', 'primary', published ? '' : 'disabled title="Сначала опубликуйте материал"') + '</div></div><div class="card" style="margin-top:17px"><h3>История экспортов</h3>' + (state.exportsList.length ? state.exportsList.map(function (x) { return '<p>' + esc(x.filename) + ' · ' + x.status + ' · файл не отправлен</p>'; }).join('') : '<p>Пока нет записей.</p>') + '</div>');
  }

  function activityPage() {
    return page('Активность', '<div class="card timeline">' + (state.activityList.length ? state.activityList.map(function (x) { return '<p>○ ' + esc(x.action) + ' — ' + new Date(x.created_at).toLocaleString('ru-RU') + '</p>'; }).join('') : '<p>Пока нет событий.</p>') + '</div>', 'Журнал действий по вашему рабочему пространству');
  }

  function settingsPage() {
    return page('Настройки', '<div class="tabs">' + ['Профиль', 'Поток', 'Безопасность', 'Опасная зона'].map(function (v) { return '<button data-settings="' + v + '" class="' + (v === state.settingsTab ? 'active' : '') + '">' + v + '</button>'; }).join('') + '</div><div class="card">' + (
      state.settingsTab === 'Профиль' ? field('profile-name', 'Имя', 'text', state.user ? state.user.name : '') + button('Сохранить', 'saveProfile')
      : state.settingsTab === 'Поток' ? (state.flow ? field('flow-name', 'Название потока', 'text', state.flow.name) + textareaField('flow-keywords', 'Ключевые слова', (state.flow.keywords || []).join('\n'), 'Хотя бы одно должно встретиться в материале. По одному на строке или через запятую.') + textareaField('flow-stopwords', 'Стоп-слова', (state.flow.stop_words || []).join('\n'), 'Материал с любым из этих слов пропускается.') + button('Сохранить', 'saveFlow') : '<p class="notice">Сначала создайте поток.</p>')
      : state.settingsTab === 'Безопасность' ? '<p>Пароли хранятся как Argon2id-хеш. Сессии — HttpOnly-cookie с CSRF-защитой.</p>'
      : '<p class="notice warning">Удаление демонстрационных данных для реального рабочего пространства не предусмотрено — это необратимо затронуло бы настоящие материалы.</p>'
    ) + '</div>');
  }

  function adminPage() {
    if (state.adminForbidden === null) {
      return page('Администрирование', '<div class="card"><p class="small">Проверяю права доступа…</p></div>');
    }
    if (state.adminForbidden) {
      return page('Администрирование', '<div class="card"><p class="notice warning">Раздел доступен только администраторам платформы (platform_admin). Ваша роль — участник рабочего пространства, а не оператор платформы.</p></div>');
    }
    return page('Администрирование', '<div class="grid3"><div class="card"><h3>Пользователи платформы</h3><strong>' + (state.adminUsers ? state.adminUsers.length : '—') + '</strong></div><div class="card"><h3>Материалов в вашем потоке</h3><strong>' + state.news.length + '</strong></div><div class="card"><h3>Экспортов</h3><strong>' + state.exportsList.length + '</strong></div></div><div class="tabs">' + ['Пользователи', 'Тарифы', 'События'].map(function (v) { return '<button data-admin="' + v + '" class="' + (v === state.adminTab ? 'active' : '') + '">' + v + '</button>'; }).join('') + '</div><div class="card">' + (
      state.adminTab === 'Пользователи' ? (state.adminUsers || []).map(function (u) { return '<p>' + esc(u.email) + (u.is_platform_admin ? ' · platform_admin' : '') + '</p>'; }).join('')
      : state.adminTab === 'Тарифы' ? (state.plansList || []).map(function (p) { return '<p>' + esc(p.name) + ' — ' + p.price_rub.toLocaleString('ru-RU') + ' ₽/мес.</p>'; }).join('')
      : (state.activityList || []).map(function (e) { return '<p>' + esc(e.action) + '</p>'; }).join('')
    ) + '</div>');
  }

  // ---------------------------------------------------------------------
  // Рендер
  // ---------------------------------------------------------------------
  function render() {
    var p = state.route, v;
    if (p === '/') v = landing();
    else if (p === '/features') v = features();
    else if (p === '/pricing') v = pricing();
    else if (p === '/faq') v = faq();
    else if (p === '/contacts') v = contacts();
    else if (p === '/privacy') v = privacy();
    else if (p === '/login') v = auth('login');
    else if (p === '/register') v = auth('register');
    else if (p === '/forgot') v = auth('forgot');
    else if (p === '/app') v = dashboard();
    else if (p === '/app/setup') v = setupPage();
    else if (p === '/app/news') v = newsPage();
    else if (p === '/app/detail') v = detailPage();
    else if (p === '/app/sources') v = sourcesPage();
    else if (p === '/app/schedule') v = schedulePage();
    else if (p === '/app/exports') v = exportsPage();
    else if (p === '/app/activity') v = activityPage();
    else if (p === '/app/settings') v = settingsPage();
    else if (p === '/app/admin') v = adminPage();
    else v = landing();
    root.innerHTML = shell(v, p.indexOf('/app') === 0);
    document.title = 'Правовой Поток — ' + (p.indexOf('/app') === 0 ? 'кабинет' : 'сайт');
    if (p === '/app/detail' && state.detail) {
      if (state.detailTab === 'Предпросмотр для читателя') loadReaderPreview();
      if (state.detailTab === 'История') loadHistorySlot();
    }
  }

  async function loadReaderPreview() {
    try {
      var d = await api('/api/v1/news/' + state.detail.id + '/public-preview');
      var slot = document.getElementById('reader-slot');
      if (!slot) return;
      slot.innerHTML = '<div class="reader"><p class="tag">Предпросмотр для читателя · как будет выглядеть на сайте</p><h1>' + esc(d.title) + '</h1><div class="article">' + esc(d.text) + '</div>' + officialBlock(d.official_document) + '<p class="small">Сайт обнаружения в публичной версии отсутствует.</p></div>';
    } catch (e) { toast(e.message, true); }
  }
  async function loadHistorySlot() {
    try {
      var events = await api('/api/v1/activity?limit=200');
      var mine = events.filter(function (e) { return e.details && e.details.news_id === state.detail.id; });
      var slot = document.getElementById('history-slot');
      if (!slot) return;
      slot.innerHTML = '<div class="timeline">' + (mine.length ? mine.map(function (e) { return '<p>○ ' + esc(e.action) + ' — ' + new Date(e.created_at).toLocaleString('ru-RU') + '</p>'; }).join('') : '<p>Событий по этому материалу пока нет.</p>') + '</div>';
    } catch (e) { toast(e.message, true); }
  }

  // ---------------------------------------------------------------------
  // Загрузка данных под маршрут
  // ---------------------------------------------------------------------
  async function ensureFlow() {
    var flows = await api('/api/v1/flows');
    state.flow = flows[0] || null;
  }

  async function loadNews() {
    if (!state.user) return;
    var qs = [];
    var st = TAB_TO_STATUS[state.newsTab];
    if (st) qs.push('status=' + encodeURIComponent(st));
    if (state.newsQuery) qs.push('q=' + encodeURIComponent(state.newsQuery));
    if (state.newsSourceFilter !== 'Все') qs.push('source_domain=' + encodeURIComponent(state.newsSourceFilter));
    state.news = await api('/api/v1/news' + (qs.length ? '?' + qs.join('&') : ''));
  }

  async function loadForRoute(route) {
    if (route.indexOf('/app') === 0) {
      if (!state.user) {
        if (state.guestBrowsing) return; // локальный просмотр фикстур, без обращений к API
        go('/login'); return;
      }
      state.access = await api('/api/v1/access');
      if (route !== '/app/setup') await ensureFlow();
      if (route === '/app' || route === '/app/news') await loadNews();
      if (route === '/app/exports') {
        state.exportsList = await api('/api/v1/exports');
        state.publishedCount = (await api('/api/v1/news?status=PUBLISHED')).length;
      }
      if (route === '/app/activity') state.activityList = await api('/api/v1/activity');
      if (route === '/app/admin') {
        try { state.adminUsers = await api('/api/v1/admin/users'); state.adminForbidden = false; }
        catch (e) { if (e.code === 'FORBIDDEN') { state.adminForbidden = true; } else throw e; }
        state.plansList = await api('/api/v1/plans');
        if (!state.activityList.length) state.activityList = await api('/api/v1/activity');
      }
      render();
    } else if (route === '/pricing') {
      state.plansList = await api('/api/v1/plans');
      render();
    }
  }

  // ---------------------------------------------------------------------
  // Модалки
  // ---------------------------------------------------------------------
  function closeModal() { modalRoot.innerHTML = ''; document.body.style.overflow = ''; confirmFn = null; if (focusBefore && document.contains(focusBefore)) focusBefore.focus(); focusBefore = null; }
  function modal(title, body, label, fn) {
    focusBefore = document.activeElement; confirmFn = fn;
    modalRoot.innerHTML = '<div class="modal-layer"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><h2 id="modal-title">' + title + '</h2>' + body + '<div class="modal-actions"><button class="btn" data-modal="cancel">Отмена</button><button class="btn primary" data-modal="ok">' + label + '</button></div></div></div>';
    document.body.style.overflow = 'hidden';
    modalRoot.querySelector('[data-modal="cancel"]').focus();
  }
  document.addEventListener('keydown', function (e) {
    if (!modalRoot.firstChild) return;
    if (e.key === 'Escape') { closeModal(); return; }
    if (e.key === 'Tab') {
      var nodes = Array.from(modalRoot.querySelectorAll('button:not(:disabled),input,textarea,select'));
      var f = nodes[0], l = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === f) { e.preventDefault(); l.focus(); }
      else if (!e.shiftKey && document.activeElement === l) { e.preventDefault(); f.focus(); }
    }
  });

  function gate() {
    if (!state.user) { modal('Сначала зарегистрируйтесь', '<p>Вы просматриваете пример. Зарегистрируйтесь и начните трёхдневное демо.</p>', 'Зарегистрироваться', function () { go('/register'); }); return false; }
    if (state.access && !state.access.can_mutate) { modal('Демонстрация завершена', '<p>Новые запуски и утверждения недоступны. Материалы остаются видны; списаний нет.</p>', 'Смотреть тарифы', function () { go('/pricing'); }); return false; }
    return true;
  }

  // ---------------------------------------------------------------------
  // Действия
  // ---------------------------------------------------------------------
  async function doRegister(name, email, password) {
    var me = await api('/api/v1/auth/register', { method: 'POST', body: { name: name, email: email, password: password } });
    state.user = me; state.guestBrowsing = false;
    toast('Регистрация выполнена — открыто трёхдневное демо');
    go('/app');
  }
  async function doLogin(email, password) {
    var me = await api('/api/v1/auth/login', { method: 'POST', body: { email: email, password: password } });
    state.user = me; state.guestBrowsing = false;
    go('/app');
  }
  async function doLogout() {
    if (state.user) {
      try { await api('/api/v1/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
    }
    state.user = null; state.access = null; state.flow = null; state.guestBrowsing = false;
    go('/');
  }

  async function collect() {
    if (!gate()) return;
    if (!state.flow) { toast('Сначала настройте поток'); return; }
    modal('Запустить сбор?', '<p>Будет запущено фоновое задание сбора материалов по источникам потока.</p>', 'Запустить', async function () {
      try {
        state.scanJobStatus = 'pending'; state.scanJobProgress = 15; render();
        var job = await api('/api/v1/flows/' + state.flow.id + '/scan-jobs', { method: 'POST', headers: { 'Idempotency-Key': uuid() } });
        var providerName = job.provider_name;
        state.scanJobProviderName = providerName;
        for (var i = 0; i < 40; i++) {
          job = await api('/api/v1/scan-jobs/' + job.id);
          state.scanJobStatus = job.status;
          state.scanJobProgress = job.status === 'done' ? 100 : job.status === 'failed' ? 100 : job.status === 'running' ? 70 : Math.min(60, 15 + i * 3);
          render();
          if (job.status === 'done' || job.status === 'failed') break;
          await sleep(700);
        }
        if (job.status === 'done') {
          var providerNote = providerName === 'fixture' ? ' (демонстрационные материалы — DEMO_FIXTURE, настоящие сайты не анализировались)' : '';
          var msg = 'Сбор завершён: добавлено материалов — ' + job.created_news_ids.length + providerNote;
          if (job.created_news_ids.length === 0 && job.duplicate_count > 0) {
            msg = 'Сбор завершён: новых материалов нет — все ' + job.duplicate_count + ' найденных уже собраны ранее (см. «Новости»)' + providerNote + '.';
          }
          toast(msg); await loadNews(); render();
        }
        else if (job.status === 'failed') toast('Задание завершилось с ошибкой: ' + (job.error || ''), true);
      } catch (e) { toast(e.message, true); }
    });
  }

  document.addEventListener('click', async function (e) {
    var m = e.target.closest('[data-modal]');
    if (m) {
      if (m.dataset.modal === 'cancel') { closeModal(); return; }
      var fn = confirmFn;
      var data = {
        domain: value('modal-domain'), period: value('modal-period'), time: value('modal-time'),
        reason: value('reject-reason'),
        official: !!(document.getElementById('check-official') || {}).checked,
        facts: !!(document.getElementById('check-facts') || {}).checked,
        newPassword: value('modal-new-password'),
        rights: !!(document.getElementById('modal-rights') || {}).checked
      };
      closeModal();
      if (fn) { try { await fn(data); } catch (err) { toast(err.message, true); } }
      return;
    }
    if (e.target.classList.contains('modal-layer')) { closeModal(); return; }

    var n = e.target.closest('[data-route]');
    if (n) { go(n.dataset.route); return; }
    var tab = e.target.closest('[data-tab]');
    if (tab) { state.newsTab = tab.dataset.tab; render(); loadNews().then(render); return; }
    var det = e.target.closest('[data-detail]');
    if (det) { state.detailTab = det.dataset.detail; render(); return; }
    var st = e.target.closest('[data-settings]');
    if (st) { state.settingsTab = st.dataset.settings; render(); return; }
    var ad = e.target.closest('[data-admin]');
    if (ad) { state.adminTab = ad.dataset.admin; render(); return; }

    var b = e.target.closest('[data-action]');
    if (!b) return;
    var a = b.dataset.action;
    try {
      if (a === 'home') return go('/');
      if (a === 'login') return go('/login');
      if (a === 'register') return go('/register');
      if (a === 'forgot') return go('/forgot');
      if (a === 'features') return go('/features');
      if (a === 'pricing') return go('/pricing');
      if (a === 'faq') return go('/faq');
      if (a === 'privacy') return go('/privacy');
      if (a === 'news') return go('/app/news');
      if (a === 'setup') return go('/app/setup');
      if (a === 'app') return go('/app');
      if (a === 'logout') return doLogout();
      if (a === 'demo') { state.user = null; state.guestBrowsing = true; go('/app'); return; }
      if (a === 'example' || a === 'open') {
        var id = a === 'example' ? currentNewsSource()[0].id : b.dataset.id;
        if (state.user) {
          state.detail = await api('/api/v1/news/' + id);
        } else {
          state.detail = Object.assign({ official_document: null, fact_passport: null, official_reviewed: false, facts_reviewed: false, version: 1 }, state.guestNews.find(function (x) { return x.id === id; }));
        }
        state.detailTab = 'Текст для публикации'; state.editingDraft = false;
        return go('/app/detail', { keepEditing: true });
      }
      if (a === 'pay') {
        var planCode = b.dataset.plan;
        if (!gate()) return;
        var order = await api('/api/v1/orders', { method: 'POST', body: { plan_code: planCode } });
        try { await api('/api/v1/orders/' + order.id + '/pay', { method: 'POST' }); }
        catch (err) {
          modal('Оплата пока недоступна', '<p>' + esc(err.message) + '</p><p class="notice">Карта не запрашивается, деньги не списываются, заказ остался в статусе «в ожидании».</p>', 'Понятно', function () {});
        }
        return;
      }
      if (a === 'collect') return collect();
      if (a === 'setupBack') { state.setupStep = Math.max(0, state.setupStep - 1); return render(); }
      if (a === 'setupNext' || a === 'setupSave') {
        if (!gate()) return;
        if (state.setupStep === 0 && !state.flow) {
          var sites = value('setup-sites');
          if (!sites || !sites.split(/\n/).every(function (z) { return /^(?:[a-z\d-]+\.)+[a-z]{2,}$/i.test(z.trim()); })) { document.getElementById('err-setup-sites').textContent = 'Укажите домены по одному на строке'; return; }
          state.setupSites = sites;
          state.setupRights = !!(document.getElementById('setup-rights') || {}).checked;
        } else if (state.setupStep === 1) {
          state.setupTheme = value('setup-theme');
          state.setupKeywords = value('setup-keywords'); state.setupStopWords = value('setup-stopwords');
          if (!state.setupTheme) { document.getElementById('err-setup-theme').textContent = 'Укажите тему'; return; }
        } else if (state.setupStep === 2) {
          state.setupPeriod = value('setup-period'); state.setupTime = value('setup-time') || '09:00';
        } else {
          var radio = document.querySelector('input[name="limit"]:checked');
          state.setupLimit = radio ? Number(radio.value) : 3;
        }
        if (a === 'setupSave') {
          if (!state.flow) {
            state.flow = await api('/api/v1/flows', { method: 'POST', body: { name: state.setupTheme, theme: state.setupTheme, schedule_period: state.setupPeriod, schedule_time: state.setupTime, news_limit_per_run: state.setupLimit, keywords: splitWords(state.setupKeywords), stop_words: splitWords(state.setupStopWords), rights_confirmed: state.setupRights, domains: state.setupSites.split(/\n/).map(function (s) { return s.trim(); }).filter(Boolean) } });
          } else {
            state.flow = await api('/api/v1/flows/' + state.flow.id, { method: 'PATCH', body: { theme: state.setupTheme, keywords: splitWords(state.setupKeywords), stop_words: splitWords(state.setupStopWords), schedule_period: state.setupPeriod, schedule_time: state.setupTime, news_limit_per_run: state.setupLimit } });
          }
          toast('Поток сохранён'); state.setupStep = 0; return go('/app');
        }
        state.setupStep++; return render();
      }
      if (a === 'edit') { if (!gate()) return; state.editingDraft = true; state.detailTab = 'Текст для публикации'; return render(); }
      if (a === 'saveDraft') {
        if (!gate()) return;
        if (!value('edit-title') || !value('edit-text')) return toast('Укажите заголовок и текст', true);
        state.detail = await api('/api/v1/news/' + state.detail.id + '/draft', { method: 'PATCH', body: { expected_version: state.detail.version, title: value('edit-title'), text: value('edit-text') } });
        state.editingDraft = false; toast('Черновик сохранён; проверьте факты заново'); return render();
      }
      if (a === 'aiDraft') {
        if (!gate()) return;
        toast('Запускаю YandexGPT Pro 5.1 (или фикстуру, если ключ не настроен)…');
        var r = await api('/api/v1/news/' + state.detail.id + '/ai/draft', { method: 'POST', body: { expected_version: state.detail.version } });
        state.detail = r.news;
        document.getElementById('edit-title') && (document.getElementById('edit-title').value = state.detail.title);
        document.getElementById('edit-text') && (document.getElementById('edit-text').value = state.detail.text);
        toast('Черновик от модели получен — обязательно проверьте перед сохранением'); return render();
      }
      if (a === 'saveOfficial') {
        if (!gate()) return;
        var name_ = value('official-title'), url_ = value('official-url');
        if (!name_ || !url_) return toast('Укажите название документа и URL', true);
        state.detail = await api('/api/v1/news/' + state.detail.id + '/official-document', { method: 'PUT', body: { expected_version: state.detail.version, title: name_, url: url_, requisites: value('official-requisites') } });
        toast('Документ сохранён, проверьте его лично'); return render();
      }
      if (a === 'saveFacts') {
        if (!gate()) return;
        if (!value('facts-edit')) return toast('Введите факты', true);
        state.detail = await api('/api/v1/news/' + state.detail.id + '/facts', { method: 'PUT', body: { expected_version: state.detail.version, text: value('facts-edit'), overall_status: value('facts-status') || 'needs_review' } });
        toast('Паспорт фактов сохранён; требуется сверка'); return render();
      }
      if (a === 'review') {
        if (!gate()) return;
        modal('Проверка редактором', '<p>Отметки делает редактор после личной проверки.</p><label style="display:block;margin:12px 0"><input id="check-official" type="checkbox" ' + (state.detail.official_reviewed ? 'checked' : '') + '> Я лично проверил официальный документ</label><label style="display:block;margin:12px 0"><input id="check-facts" type="checkbox" ' + (state.detail.facts_reviewed ? 'checked' : '') + '> Я лично сверил факты и выводы</label>', 'Сохранить отметки', async function (data) {
          state.detail = await api('/api/v1/news/' + state.detail.id + '/review', { method: 'POST', body: { official_reviewed: data.official, facts_reviewed: data.facts } });
          toast(data.official && data.facts ? 'Готово к утверждению' : 'Проверка не завершена'); render();
        });
        return;
      }
      if (a === 'approve') {
        if (!gate()) return;
        state.detail = await api('/api/v1/news/' + state.detail.id + '/approve', { method: 'POST' });
        toast('Материал утверждён'); return render();
      }
      if (a === 'publish') {
        if (!gate()) return;
        modal('Опубликовать материал?', '<p>Появится в XML-экспорте и публичном предпросмотре.</p>', 'Опубликовать', async function () {
          state.detail = await api('/api/v1/news/' + state.detail.id + '/publish', { method: 'POST' });
          toast('Материал опубликован'); render();
        });
        return;
      }
      if (a === 'reject') {
        if (!gate()) return;
        modal('Отклонить материал?', '<div class="field"><label for="reject-reason">Причина</label><textarea id="reject-reason"></textarea></div>', 'Отклонить', async function (data) {
          state.detail = await api('/api/v1/news/' + state.detail.id + '/reject', { method: 'POST', body: { reason: data.reason || 'без указания причины' } });
          toast('Материал отклонён'); render();
        });
        return;
      }
      if (a === 'addSource' || a === 'editSource') {
        if (!gate()) return;
        var srcId = b.dataset.id, existing = srcId ? (state.flow.sources || []).find(function (s) { return s.id === srcId; }) : null;
        var rightsBox = '<label class="small" style="display:flex;gap:9px;margin:12px 0;align-items:flex-start"><input type="checkbox" id="modal-rights" style="margin-top:3px"><span>Подтверждаю, что вправе использовать материалы этого сайта. Система проверит robots.txt, сама найдёт ленту новостей (RSS, sitemap или список статей) и будет собирать только заголовки, анонсы и факты — без хранения полного текста.</span></label>';
        modal(a === 'addSource' ? 'Добавить сайт' : 'Изменить сайт', field('modal-domain', 'Домен (например, example.ru)', 'text', existing ? existing.domain : '') + rightsBox, 'Сохранить', async function (data) {
          var domain = String(data.domain || '').trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
          if (!/^(?:[a-z\d-]+\.)+[a-z]{2,}$/i.test(domain)) return toast('Некорректный домен', true);
          toast('Проверяем сайт: robots.txt и лента новостей — до минуты…');
          var body = { domain: domain, rights_confirmed: !!data.rights };
          if (a === 'editSource') await api('/api/v1/flows/' + state.flow.id + '/sources/' + srcId, { method: 'PATCH', body: body });
          else await api('/api/v1/flows/' + state.flow.id + '/sources', { method: 'POST', body: body });
          await ensureFlow();
          var added = (state.flow.sources || []).find(function (s) { return s.domain === domain; });
          if (added && added.status !== 'ready') toast('Сайт добавлен, но сбор пока невозможен: ' + added.status_note, true);
          else toast('Сайт сохранён: ' + (added ? (SOURCE_KIND_LABELS[added.kind] || 'сбор настроен') : 'готово'));
          render();
        });
        return;
      }
      if (a === 'deleteSource') {
        if (!gate()) return;
        var delId = b.dataset.id;
        modal('Удалить источник?', '<p>Сайт будет удалён из потока.</p>', 'Удалить', async function () {
          await api('/api/v1/flows/' + state.flow.id + '/sources/' + delId, { method: 'DELETE' });
          await ensureFlow(); toast('Источник удалён'); render();
        });
        return;
      }
      if (a === 'changeSchedule') {
        if (!gate()) return;
        modal('Изменить расписание', '<div class="field"><label for="modal-period">Периодичность</label><select id="modal-period">' + allowedSchedulePeriods().map(function (v) { return '<option ' + (v === state.flow.schedule_period ? 'selected' : '') + '>' + v + '</option>'; }).join('') + '</select></div>' + field('modal-time', 'Время (Москва)', 'time', state.flow.schedule_time), 'Сохранить', async function (data) {
          state.flow = await api('/api/v1/flows/' + state.flow.id, { method: 'PATCH', body: { schedule_period: data.period, schedule_time: data.time || '09:00' } });
          toast('Расписание сохранено'); render();
        });
        return;
      }
      if (a === 'exportXml') {
        if (!gate()) return;
        var filename = value('xml-name') || 'legal-news.xml';
        var exp = await api('/api/v1/exports', { method: 'POST', body: { filename: filename } });
        var dl = await api('/api/v1/exports/' + exp.id + '/download');
        modal('Экспорт XML (' + dl.status + ')', '<p class="small">Файл не отправлен: интеграция с Яндекс.Диском не подключена.</p><pre class="xml">' + esc(dl.content) + '</pre>', 'Закрыть', function () {});
        state.exportsList = await api('/api/v1/exports'); render();
        return;
      }
      if (a === 'saveProfile') {
        if (!gate()) return;
        var pn = value('profile-name'); if (!pn) return toast('Заполните поле', true);
        state.user = await api('/api/v1/auth/me', { method: 'PATCH', body: { name: pn } });
        toast('Профиль сохранён'); return render();
      }
      if (a === 'saveFlow') {
        if (!gate()) return;
        var fn = value('flow-name'); if (!fn) return toast('Заполните поле', true);
        state.flow = await api('/api/v1/flows/' + state.flow.id, { method: 'PATCH', body: { name: fn, keywords: splitWords(value('flow-keywords')), stop_words: splitWords(value('flow-stopwords')) } });
        toast('Настройки потока сохранены'); return render();
      }
    } catch (err) {
      if (err.code === 'TRIAL_EXPIRED') { modal('Демонстрация завершена', '<p>' + esc(err.message) + '</p>', 'Смотреть тарифы', function () { go('/pricing'); }); }
      else if (err.code === 'VERSION_CONFLICT') { toast('Материал изменён другим пользователем — обновляю…', true); if (state.detail) state.detail = await api('/api/v1/news/' + state.detail.id); render(); }
      else toast(err.message, true);
    }
  });

  document.addEventListener('input', function (e) {
    if (e.target.id === 'search') { state.newsQuery = e.target.value; loadNews().then(function () { document.getElementById('news-results').innerHTML = newsRowsHtml(); }); }
    if (e.target.id === 'edit-text') { var c = document.getElementById('counter'); if (c) c.textContent = 'Символов: ' + e.target.value.length; }
  });
  document.addEventListener('change', function (e) {
    if (e.target.id === 'source-filter') { state.newsSourceFilter = e.target.value; loadNews().then(function () { document.getElementById('news-results').innerHTML = newsRowsHtml(); }); }
    if (e.target.id === 'setup-category' && e.target.value) {
      var themeInput = document.getElementById('setup-theme');
      if (themeInput) themeInput.value = e.target.value;
    }
    if (e.target.hasAttribute('data-toggle')) {
      if (!gate()) { e.target.checked = !e.target.checked; return; }
      var id = e.target.dataset.toggle, checked = e.target.checked;
      api('/api/v1/flows/' + state.flow.id + '/sources/' + id, { method: 'PATCH', body: { active: checked } })
        .then(function () { return ensureFlow(); }).then(function () { toast('Настройка сайта сохранена'); render(); })
        .catch(function (err) { toast(err.message, true); e.target.checked = !checked; });
    }
    if (e.target.name === 'limit') state.setupLimit = Number(e.target.value);
  });

  document.addEventListener('submit', async function (e) {
    try {
      if (e.target.id === 'auth-form') {
        e.preventDefault();
        var kind = e.target.dataset.kind, email = value('auth-email');
        if (!/^\S+@\S+\.\S+$/.test(email)) { document.getElementById('err-auth-email').textContent = 'Укажите корректный email'; return; }
        if (kind === 'forgot') {
          var resp = await api('/api/v1/auth/forgot-password', { method: 'POST', body: { email: email } });
          if (resp.dev_reset_token) {
            document.getElementById('auth-extra').innerHTML = '<div class="notice" style="margin-top:14px">Демо-режим (почта не подключена): ' + field('reset-new-password', 'Новый пароль', 'password', '') + button('Установить новый пароль', 'doReset', 'primary') + '</div>';
            document.getElementById('auth-extra').dataset.token = resp.dev_reset_token;
          } else {
            toast('Если такой email зарегистрирован, на него отправлена ссылка (почта пока не подключена).');
          }
          return;
        }
        if (!value('auth-password')) { document.getElementById('err-auth-password').textContent = 'Укажите пароль'; return; }
        if (kind === 'register') {
          if (!value('auth-name')) { document.getElementById('err-auth-name').textContent = 'Укажите имя'; return; }
          if (value('auth-password') !== value('auth-repeat')) { document.getElementById('err-auth-repeat').textContent = 'Пароли не совпадают'; return; }
          if (!document.getElementById('auth-ok').checked) return toast('Подтвердите ознакомление с условиями демо', true);
          await doRegister(value('auth-name'), email, value('auth-password'));
          return;
        }
        await doLogin(email, value('auth-password'));
      }
      if (e.target.id === 'contact-form') { e.preventDefault(); toast('Форма демо: сообщение не отправлено (почта не подключена)'); }
    } catch (err) {
      if (err.code === 'VALIDATION_ERROR') toast(err.message, true);
      else if (err.code === 'UNAUTHORIZED') toast('Неверный email или пароль', true);
      else if (err.code === 'RATE_LIMITED') toast(err.message, true);
      else toast(err.message, true);
    }
  });

  document.addEventListener('click', async function (e) {
    var btn = e.target.closest('[data-action="doReset"]');
    if (!btn) return;
    var extra = document.getElementById('auth-extra');
    var token = extra && extra.dataset.token;
    var pw = value('reset-new-password');
    if (!token || !pw) return;
    try {
      await api('/api/v1/auth/reset-password', { method: 'POST', body: { token: token, new_password: pw } });
      toast('Пароль обновлён — теперь можно войти'); go('/login');
    } catch (err) { toast(err.message, true); }
  });

  // ---------------------------------------------------------------------
  // Загрузка приложения
  // ---------------------------------------------------------------------
  async function boot() {
    try {
      state.user = await api('/api/v1/auth/me');
      go('/app');
    } catch (e) {
      go('/');
    }
  }
  boot();
})();
