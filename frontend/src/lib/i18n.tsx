"use client";

import { createContext, useCallback, useContext, useEffect, type ReactNode } from "react";

import { usePref } from "@/lib/prefs";

export type Lang = "ar" | "en";

const DICT = {
  appName: { ar: "NexTrade AI", en: "NexTrade AI" },
  tagline: { ar: "منصة بحث وتداول ورقي على ناسداك", en: "Nasdaq research & paper-trading platform" },
  navOverview: { ar: "الرئيسية", en: "Overview" },
  navAccounts: { ar: "حسابات MT5", en: "MT5 accounts" },
  navLive: { ar: "المراقبة الحية", en: "Live monitor" },
  navAnalysis: { ar: "التحليل", en: "Analysis" },
  navSettings: { ar: "الإعدادات", en: "Settings" },
  modeSim: { ar: "محاكاة / تداول ورقي", en: "SIMULATION / PAPER" },
  realLocked: { ar: "التداول الحقيقي: مقفل", en: "Real trading: LOCKED" },
  notConnected: { ar: "غير متصل", en: "Not connected" },
  connected: { ar: "متصل", en: "Connected" },
  backendDown: { ar: "الخادم المحلي لا يستجيب — شغّل الخادم ثم أعد المحاولة", en: "Local backend not responding — start it and retry" },
  loading: { ar: "جارٍ التحميل…", en: "Loading…" },
  save: { ar: "حفظ", en: "Save" },
  reset: { ar: "استعادة الافتراضي", en: "Reset to default" },
  cancel: { ar: "إلغاء", en: "Cancel" },
  delete: { ar: "حذف", en: "Delete" },
  connect: { ar: "اتصال", en: "Connect" },
  disconnect: { ar: "قطع الاتصال", en: "Disconnect" },
  start: { ar: "تشغيل", en: "Start" },
  stop: { ar: "إيقاف", en: "Stop" },
  run: { ar: "تشغيل التحليل", en: "Run analysis" },
  simulatedNote: { ar: "كل الصفقات هنا ورقية (محاكاة) — لا تُرسل أي أوامر للوسيط", en: "All trades here are paper (simulated) — no order ever reaches the broker" },
  mockNote: { ar: "بيانات وهمية (MOCK) — لتجربة المنصة فقط، ليست دليلاً على السوق", en: "MOCK data — for trying the platform only, not market evidence" },
  // overview
  connection: { ar: "الاتصال", en: "Connection" },
  account: { ar: "الحساب", en: "Account" },
  server: { ar: "الخادم", en: "Server" },
  company: { ar: "الشركة", en: "Company" },
  accountMode: { ar: "نوع الحساب", en: "Account type" },
  readOnly: { ar: "قراءة فقط", en: "Read-only" },
  canTrade: { ar: "يستطيع التداول", en: "Can trade" },
  symbol: { ar: "الرمز", en: "Symbol" },
  liveStatus: { ar: "المراقبة الحية", en: "Live monitor" },
  recentRuns: { ar: "آخر التحليلات", en: "Recent analyses" },
  recentErrors: { ar: "أخطاء حديثة", en: "Recent errors" },
  none: { ar: "لا يوجد", en: "None" },
  goAccounts: { ar: "اربط حساب MT5 من صفحة الحسابات", en: "Connect an MT5 account on the Accounts page" },
  serverTime: { ar: "توقيت خادم الوسيط", en: "Broker server time" },
  undetermined: { ar: "لم يُحدَّد بعد — يُحدَّد تلقائياً عند أول تحليل", en: "Not determined yet — decided on the first analysis" },
  // accounts
  addAccount: { ar: "إضافة حساب", en: "Add account" },
  label: { ar: "اسم تختاره", en: "Label" },
  serverHint: { ar: "كما يظهر في نافذة تسجيل الدخول في MT5، مثل PrimeXCapitalLtd-Server", en: "As shown in the MT5 login window, e.g. PrimeXCapitalLtd-Server" },
  login: { ar: "رقم الحساب", en: "Account number" },
  terminalPath: { ar: "مسار terminal64.exe (اختياري)", en: "terminal64.exe path (optional)" },
  terminalPathHint: { ar: "اتركه فارغاً إذا كان MT5 واحداً فقط على الجهاز", en: "Leave empty if there is only one MT5 on this PC" },
  passwordNeverSaved: { ar: "كلمة المرور لا تُحفظ أبداً — تُكتب عند الاتصال فقط", en: "The password is never saved — typed only when connecting" },
  addMock: { ar: "إضافة وسيط تجريبي (MOCK) لتجربة المنصة", en: "Add a MOCK broker to try the platform" },
  method: { ar: "طريقة الاتصال", en: "Method" },
  methodCredentials: { ar: "بيانات الحساب (رقم + كلمة مرور)", en: "Account credentials" },
  methodAttach: { ar: "MT5 المفتوح على هذا الجهاز", en: "The MT5 already open on this PC" },
  password: { ar: "كلمة المرور", en: "Password" },
  investorAdvice: { ar: "ننصح بكلمة مرور المستثمر (Investor): تقرأ الأسعار ولا تستطيع فتح صفقات", en: "Use the investor password: it reads prices but cannot trade" },
  chooseSymbol: { ar: "اختر رمز ناسداك", en: "Choose the Nasdaq symbol" },
  showAllSymbols: { ar: "عرض كل الرموز", en: "Show all symbols" },
  spec: { ar: "مواصفات العقد (من MT5)", en: "Contract specification (from MT5)" },
  specCompare: { ar: "قارن هذه القيم بنافذة Specification في MT5", en: "Compare with the Specification window in MT5" },
  missing: { ar: "ناقص", en: "missing" },
  select: { ar: "اختيار", en: "Select" },
  mockBroker: { ar: "وسيط تجريبي (MOCK)", en: "MOCK broker" },
  // live
  session: { ar: "الجلسة", en: "Session" },
  nyOpen: { ar: "افتتاح نيويورك", en: "NY open" },
  baghdadTime: { ar: "بتوقيت بغداد", en: "Baghdad time" },
  phase: { ar: "المرحلة", en: "Phase" },
  orRange: { ar: "نطاق الافتتاح", en: "Opening range" },
  direction: { ar: "الاتجاه", en: "Direction" },
  dataAge: { ar: "عمر آخر شمعة", en: "Last bar age" },
  paperBalance: { ar: "الرصيد الورقي", en: "Paper balance" },
  openPositions: { ar: "صفقات ورقية مفتوحة", en: "Open paper positions" },
  events: { ar: "سجل القرارات", en: "Decision log" },
  trades: { ar: "الصفقات الورقية", en: "Paper trades" },
  quote: { ar: "السعر الآن", en: "Quote" },
  spread: { ar: "السبريد", en: "Spread" },
  todayChart: { ar: "شارت الجلسة", en: "Session chart" },
  // analysis
  from: { ar: "من", en: "From" },
  to: { ar: "إلى", en: "To" },
  split: { ar: "جزء البيانات", en: "Data split" },
  splitHelp: { ar: "التطوير لضبط الإعدادات · التحقق لمراجعتها · الاختبار النهائي يُستخدم مرة واحدة فقط", en: "Dev to tune · Validation to check · Holdout is used once only" },
  running: { ar: "جارٍ التحليل… (قد يستغرق دقيقة)", en: "Running… (may take a minute)" },
  report: { ar: "فتح التقرير المرئي", en: "Open visual report" },
  costSensitivity: { ar: "حساسية التكاليف", en: "Cost sensitivity" },
  quality: { ar: "جودة البيانات", en: "Data quality" },
  // settings
  sec_risk: { ar: "المخاطر (تُطبَّق في الكود)", en: "Risk (enforced in code)" },
  sec_strategy: { ar: "الاستراتيجية: اختراق نطاق الافتتاح + إعادة الاختبار", en: "Strategy: ORB + Retest" },
  sec_costs: { ar: "افتراضات التكاليف", en: "Cost assumptions" },
  sec_paper: { ar: "الحساب الورقي", en: "Paper account" },
  configHash: { ar: "بصمة الإعدادات", en: "Config hash" },
  savedStopsLive: { ar: "تم الحفظ. أُوقفت المراقبة الحية لتُعاد بالإعدادات الجديدة.", en: "Saved. The live monitor was stopped to restart with the new settings." },
  saved: { ar: "تم الحفظ", en: "Saved" },
} as const;

export type Key = keyof typeof DICT;

type Ctx = { lang: Lang; t: (k: Key) => string; setLang: (l: Lang) => void };
const I18n = createContext<Ctx>({ lang: "ar", t: (k) => DICT[k].ar, setLang: () => {} });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = usePref<Lang>("nextrade.lang", ["ar", "en"], "ar");

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  }, [lang]);

  const t = useCallback((k: Key) => DICT[k][lang], [lang]);
  return <I18n.Provider value={{ lang, t, setLang }}>{children}</I18n.Provider>;
}

export const useI18n = () => useContext(I18n);

/** Human labels for API reason codes (fallback: the code itself). */
const REASONS: Record<string, { ar: string; en: string }> = {
  NO_DATA: { ar: "لا توجد بيانات", en: "No data" },
  INCOMPLETE_OPENING_RANGE: { ar: "نطاق الافتتاح ناقص", en: "Opening range incomplete" },
  NO_BREAKOUT: { ar: "لا اختراق قبل الموعد", en: "No breakout before deadline" },
  NO_RETEST: { ar: "لا إعادة اختبار", en: "No retest" },
  RETEST_EXPIRED: { ar: "إعادة الاختبار لم تتأكد", en: "Retest not confirmed" },
  FALSE_BREAKOUT: { ar: "اختراق كاذب", en: "False breakout" },
  STOP_DISTANCE_INVALID: { ar: "مسافة الوقف خارج الحدود", en: "Stop distance out of bounds" },
  ENTRY_WINDOW_CLOSED: { ar: "انتهت نافذة الدخول", en: "Entry window closed" },
  DATA_GAP: { ar: "فجوة في البيانات", en: "Data gap" },
  STALE_DATA: { ar: "بيانات متأخرة", en: "Stale data" },
  SPREAD_TOO_WIDE: { ar: "السبريد أوسع من الحد", en: "Spread too wide" },
  SPREAD_LIMIT_NOT_CONFIGURED: { ar: "حد السبريد غير مضبوط في الإعدادات", en: "Spread limit not configured" },
  DAILY_LOSS_LIMIT: { ar: "حد الخسارة اليومية", en: "Daily loss limit" },
  DAILY_LOSS_BUDGET_INSUFFICIENT: { ar: "ميزانية الخسارة اليومية لا تكفي", en: "Daily loss budget insufficient" },
  TRADE_LIMIT: { ar: "حد عدد الصفقات", en: "Trade limit" },
  POSITION_LIMIT: { ar: "حد المراكز المفتوحة", en: "Position limit" },
  MIN_LOT_EXCEEDS_RISK: { ar: "أصغر لوت يتجاوز سقف المخاطرة", en: "Minimum lot exceeds risk cap" },
  SPEC_MISSING: { ar: "مواصفات العقد ناقصة", en: "Contract spec missing" },
  REAL_TRADING_LOCKED: { ar: "التداول الحقيقي مقفل", en: "Real trading locked" },
  TRADING_DISABLED: { ar: "مفتاح الإيقاف مفعّل", en: "Kill switch on" },
  TARGET: { ar: "الهدف", en: "Target" },
  STOP: { ar: "الوقف", en: "Stop" },
  TIME_EXIT: { ar: "خروج زمني", en: "Time exit" },
  SESSION_END: { ar: "نهاية الجلسة", en: "Session end" },
  ENTRY_BAR_MISSING: { ar: "شمعة الدخول غير متوفرة", en: "Entry bar missing" },
  ENTRY_INVALID_AT_FILL: { ar: "السعر تجاوز الوقف/الهدف عند الدخول", en: "Price beyond stop/target at fill" },
  SERVER_TIME_UNDETERMINED: { ar: "لم يُحدَّد توقيت خادم الوسيط بثقة", en: "Broker server time not determined" },
  NOT_CONNECTED: { ar: "غير متصل بحساب MT5", en: "Not connected to MT5" },
  NO_SYMBOL: { ar: "لم يُختر رمز ناسداك بعد", en: "No Nasdaq symbol selected" },
  HOLDOUT_ALREADY_USED: { ar: "الاختبار النهائي استُخدم سابقاً", en: "Holdout already used" },
  MT5_PACKAGE_MISSING: { ar: "مكتبة MetaTrader5 غير مثبتة", en: "MetaTrader5 package missing" },
  CONNECT_FAILED: { ar: "فشل الاتصال", en: "Connection failed" },
  NO_HISTORY: { ar: "لا يوجد تاريخ أسعار لهذا الرمز", en: "No price history for this symbol" },
  ANALYSIS_BUSY: { ar: "يوجد تحليل آخر قيد التشغيل", en: "Another analysis is running" },
  GATEWAY_ERROR: { ar: "خطأ من MT5", en: "MT5 error" },
  INTERNAL_ERROR: { ar: "خطأ داخلي", en: "Internal error" },
  DATA_QUALITY: { ar: "جودة البيانات غير مقبولة", en: "Data quality not acceptable" },
  SPEC_INCOMPLETE: { ar: "مواصفات العقد ناقصة", en: "Contract spec incomplete" },
};

export function reasonLabel(code: string, lang: Lang): string {
  return REASONS[code]?.[lang] ?? code;
}
