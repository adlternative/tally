import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zh from "./locales/zh.json";
import en from "./locales/en.json";
import { preference, remember } from "./api";

i18n.use(initReactI18next).init({
  resources: { zh: { translation: zh }, en: { translation: en } },
  lng: preference("jev.lang", "zh") === "en" ? "en" : "zh",
  fallbackLng: "zh",
  interpolation: { escapeValue: false },
});
i18n.on("languageChanged", (language) => {
  document.documentElement.lang = language === "en" ? "en" : "zh-CN";
  remember("jev.lang", language);
});
document.documentElement.lang = i18n.language === "en" ? "en" : "zh-CN";
export default i18n;
