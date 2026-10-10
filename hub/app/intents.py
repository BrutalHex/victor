"""Stock Vector voice commands (DDL / wire-pod intent set) on the hub.

The transcript is normalised and matched against whole-utterance patterns in
English, German and Persian. A match runs the command directly (no chat
call): a short spoken reply where stock Vector spoke, a DDL face clip, and/or
a named robot action ("act" on the 7443 skill channel, executed by the
agent's action runner under the on-robot veto). No match -> normal chat.

Intent names follow wire-pod's chipper/intent-data (intent_imperative_*,
intent_play_*, ...). STATUS says what this robot can really do today.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------- normalise
_FA_MAP = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": " ", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا"})
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_LEAD = re.compile(
    r"^(?:(?:hey|hi|ok|okay|hallo|he|ey|سلام|هی)\s+)?(?:vector|victor|vektor|viktor|wektor|ویکتور|وکتور)\b[\s,]*"
)
_POLITE = re.compile(
    r"\b(?:please|pls|bitte|mal|doch|can you|could you|would you|will you|kannst du|könntest du|"
    r"لطفا|لطفاً|میشه|می شه|میتونی|می تونی)\b"
)
_TAIL = re.compile(r"[\s,]*(?:vector|victor|vektor|viktor|ویکتور|وکتور)$")
_FILLER_LEAD = re.compile(r"^(?:(?:ok|okay|so|now|um|uh|and|also|hey|alright|all right|also|jetzt|und|also|خب|حالا|الان|و)\s+)+")
_FILLER_TAIL = re.compile(r"(?:\s+(?:now|again|for me|right now|real quick|jetzt|nochmal|mal|الان|دوباره))+$")


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower().translate(_FA_MAP).translate(_FA_DIGITS)
    t = t.replace("’", "'")
    t = re.sub(r"[^\w\s']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _LEAD.sub("", t)
    t = _TAIL.sub("", t)
    t = _POLITE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _FILLER_LEAD.sub("", t)
    t = _LEAD.sub("", t)  # "okay vector, ..."
    t = _TAIL.sub("", t)
    if t not in ("time",):
        t = _FILLER_TAIL.sub("", t)
    return t.strip()


# ---------------------------------------------------------------- intents
@dataclass
class Intent:
    name: str
    lang: str
    arg: str = ""
    text: str = ""


# (name, status, note, {lang: [whole-utterance regex, ...]})
_DEF: list[tuple[str, str, str, dict[str, list[str]]]] = [
    ("intent_system_charger", "not feasible yet", "needs charger-marker vision and docking control", {
        "en": [r"(?:go|drive|get) (?:back )?(?:to )?(?:your |the )?(?:charger|home|charging station)",
               r"go home", r"find (?:your |the )?charger", r"(?:go )?(?:dock|charge)(?: yourself)?"],
        "de": [r"(?:geh|fahr|fahre) (?:zu |zur |zurück zur |an |auf )?(?:deine[rnm]? |die )?(?:ladestation|ladegerät|lader|ladeschale)",
               r"(?:geh|fahr) nach hause", r"lade dich(?: auf)?"],
        "fa": [r"(?:برو|بر گرد|برگرد) (?:سر |به |روی )?(?:شارژر|شارژ)(?:ت)?", r"برو خونه", r"برو شارژ شو"],
    }),
    ("intent_system_leavecharger", "works (needs a human to check)", "drives ~9 cm forward off the contacts", {
        "en": [r"(?:get|go|drive|come|roll) off (?:of )?(?:your |the )?(?:charger|dock|charging station)",
               r"leave (?:your |the )?(?:charger|dock)"],
        "de": [r"(?:geh|fahr|fahre|komm) (?:von |runter von )?(?:deiner |der )?(?:ladestation|ladeschale)(?: runter| herunter)?",
               r"verlass (?:deine |die )?ladestation"],
        "fa": [r"(?:از )?(?:روی )?شارژر (?:بیا|برو) (?:پایین|بیرون|کنار)", r"از شارژر (?:بیا|برو)(?: پایین| بیرون)?"],
    }),
    ("intent_imperative_come", "works off the charger", "creeps ~10 cm forward (no person tracking)", {
        "en": [r"come (?:here|to me|over here|over)"],
        "de": [r"komm (?:her|hierher|zu mir|mal her)"],
        "fa": [r"بیا (?:اینجا|پیش من|این ور|اینور)", r"بیا"],
    }),
    ("intent_imperative_forward", "works off the charger", "~6 cm", {
        "en": [r"(?:move|go|drive) (?:forward|forwards|ahead|straight)", r"forward"],
        "de": [r"(?:fahr|fahre|geh) (?:nach )?(?:vorne|vorwärts|geradeaus)", r"vorwärts"],
        "fa": [r"(?:برو|حرکت کن) (?:جلو|به جلو|جلوتر)"],
    }),
    ("intent_imperative_backup", "works off the charger", "~5 cm", {
        "en": [r"(?:back up|backup|go back|move back|drive back|reverse|back off)(?: a bit| a little)?", r"backwards?"],
        "de": [r"(?:fahr|fahre|geh) (?:zurück|rückwärts)(?: ein bisschen| etwas)?", r"rückwärts"],
        "fa": [r"(?:برو|بیا) (?:عقب|عقبتر|عقب تر)"],
    }),
    ("intent_imperative_turnleft", "works off the charger", "90 degrees", {
        "en": [r"(?:turn|go|rotate) (?:to the )?left"],
        "de": [r"(?:dreh dich |fahr |geh )?(?:nach )?links(?: drehen)?", r"dreh dich nach links"],
        "fa": [r"(?:بپیچ|برو|بچرخ) (?:به )?(?:سمت )?چپ", r"(?:به )?(?:سمت )?چپ (?:بپیچ|بچرخ|برو)"],
    }),
    ("intent_imperative_turnright", "works off the charger", "90 degrees", {
        "en": [r"(?:turn|go|rotate) (?:to the )?right"],
        "de": [r"(?:dreh dich |fahr |geh )?(?:nach )?rechts(?: drehen)?", r"dreh dich nach rechts"],
        "fa": [r"(?:بپیچ|برو|بچرخ) (?:به )?(?:سمت )?راست", r"(?:به )?(?:سمت )?راست (?:بپیچ|بچرخ|برو)"],
    }),
    ("intent_imperative_turnaround", "works off the charger", "180 degrees", {
        "en": [r"turn (?:around|round)", r"do a one eighty"],
        "de": [r"dreh dich (?:um|herum)", r"umdrehen"],
        "fa": [r"دور بزن", r"برگرد عقب", r"بچرخ"],
    }),
    ("intent_imperative_lookatme", "partial", "head up + look-at clip; no face search/turn", {
        "en": [r"look at me", r"stare at me", r"look (?:over )?here", r"look up"],
        "de": [r"(?:schau|guck|sieh) (?:mich )?(?:an|her|zu mir)", r"(?:schau|guck|sieh) mich an", r"schau nach oben"],
        "fa": [r"(?:به )?من(?:و)? نگاه کن", r"منو ببین", r"نگام کن", r"بالا رو نگاه کن"],
    }),
    ("intent_play_fistbump", "partial", "lift up + bump detect off the charger; on the charger face only (lift locked there)", {
        "en": [r"(?:give me a |let's |lets |do a )?(?:fist ?bump|fist pump|bump it|high five|give me five|give me a high five)"],
        "de": [r"(?:gib mir (?:eine |ne )?)?(?:faust|ghettofaust|fist ?bump|check|high five|fünf)"],
        "fa": [r"(?:بزن )?قدش", r"مشت بزن", r"بزن به مشتم", r"های فایو"],
    }),
    ("intent_photo_take_extend", "works", "saves the camera frame on the hub (/app/data/photos)", {
        "en": [r"(?:take|snap|make) (?:a |an )?(?:picture|photo|pic|selfie|snapshot)(?: of (?:me|us|this))?"],
        "de": [r"(?:mach|nimm|schieß) (?:ein |mal ein )?(?:foto|bild|selfie)(?: von (?:mir|uns))?"],
        "fa": [r"(?:یه |یک )?عکس (?:بگیر|بنداز|بگیر از من)", r"از من (?:یه |یک )?عکس بگیر"],
    }),
    ("intent_clock_checktimer", "works", "", {
        "en": [r"(?:check|how much time is left on) (?:the |my )?timer", r"how long (?:is )?left(?: on (?:the|my) timer)?"],
        "de": [r"wie lange (?:noch|läuft der timer noch)", r"wie viel zeit (?:ist )?noch(?: auf dem timer)?"],
        "fa": [r"تایمر چقدر (?:مونده|مانده)", r"چقدر (?:از تایمر )?(?:مونده|مانده)"],
    }),
    ("intent_global_stop_extend", "works", "cancels the timer", {
        "en": [r"(?:stop|cancel|clear|end) (?:the |my )?timer"],
        "de": [r"(?:stopp|stoppe|stop|beende|lösch|lösche) (?:den )?timer", r"timer (?:stopp|stoppen|abbrechen|aus)"],
        "fa": [r"تایمر (?:رو |را )?(?:لغو|قطع|خاموش|کنسل) کن"],
    }),
    ("intent_clock_settimer_extend", "works", "hub-side timer, spoken alarm", {
        "en": [r"(?:set|start) (?:a |the )?timer (?:for )?(?P<n>.+)", r"timer (?:for )?(?P<n>.+)"],
        "de": [r"(?:stell|setz|starte?) (?:einen |den )?(?:timer|wecker) (?:auf |für )?(?P<n>.+)", r"timer (?:auf |für )?(?P<n>.+)"],
        "fa": [r"(?:یه |یک )?تایمر (?:برای )?(?P<n>.+?) (?:بذار|بزار|تنظیم کن|بگذار)", r"تایمر (?P<n>.+)"],
    }),
    ("intent_clock_time", "works", "spoken; no clock face", {
        "en": [r"what time is it(?: now)?", r"what's the time", r"what is the time", r"tell me the time", r"time"],
        "de": [r"wie spät ist es(?: jetzt)?", r"wie viel uhr ist es", r"wieviel uhr ist es", r"uhrzeit"],
        "fa": [r"ساعت چند(?:ه| است| هست)?", r"الان ساعت چنده"],
    }),
    ("intent_character_age", "partial", "needs HUB_ROBOT_BIRTHDAY in .env", {
        "en": [r"how old are you", r"what's your age", r"what is your age", r"when is your birthday"],
        "de": [r"wie alt bist du", r"wann hast du geburtstag"],
        "fa": [r"چند سالته", r"چند سالت(?:ه| است)", r"سنت چقدره"],
    }),
    ("intent_imperative_dance", "partial", "short dance (wiggle off the charger, head only on it); no beat detection", {
        "en": [r"(?:dance|let's dance|lets dance|do a dance|dance for me|dance to the (?:beat|music))"],
        "de": [r"tanz(?:e)?(?: mal| für mich)?", r"lass uns tanzen"],
        "fa": [r"برقص", r"(?:یه |یک )?رقص (?:کن|بکن)"],
    }),
    ("intent_play_anytrick", "partial", "plays the dance", {
        "en": [r"do (?:a |some )?tricks?", r"do something cool", r"show me (?:a )?tricks?"],
        "de": [r"mach (?:einen |ein )?(?:trick|kunststück)", r"zeig mir (?:einen )?trick"],
        "fa": [r"(?:یه |یک )?(?:حرکت|کار) (?:باحال|جالب) (?:بکن|کن)", r"شیرین کاری کن"],
    }),
    ("intent_imperative_praise", "works", "goodrobot clip (stock: no speech)", {
        "en": [r"good (?:robot|boy|girl|job|vector)", r"well done", r"(?:you're|you are) (?:awesome|amazing|great|the best)", r"awesome"],
        "de": [r"(?:guter|braver) (?:roboter|junge)", r"gut gemacht", r"super gemacht", r"braver", r"du bist (?:toll|super|der beste)"],
        "fa": [r"آفرین(?: پسر خوب)?", r"ربات خوب", r"باریکلا", r"عالی بود", r"دمت گرم"],
    }),
    ("intent_imperative_abuse", "works", "badrobot clip (stock: no speech)", {
        "en": [r"bad (?:robot|boy|vector)", r"(?:you're|you are) (?:stupid|dumb|bad|terrible|horrible)", r"i hate you"],
        "de": [r"(?:böser|schlechter|blöder) roboter", r"du bist (?:blöd|dumm|doof|schlecht)", r"ich hasse dich"],
        "fa": [r"ربات بد", r"(?:تو )?(?:خنگی|احمقی|بدی)", r"ازت متنفرم"],
    }),
    ("intent_imperative_apologize", "works", "apology clip", {
        "en": [r"(?:i'm |i am )?sorry(?: vector)?", r"i apologi[sz]e", r"my bad"],
        "de": [r"(?:es )?tut mir leid", r"entschuldigung", r"sorry"],
        "fa": [r"ببخشید", r"متاسفم", r"معذرت می ?خوام"],
    }),
    ("intent_imperative_love", "works", "iloveyou clip", {
        "en": [r"i love you(?: vector| too)?", r"love you"],
        "de": [r"ich liebe dich", r"ich hab dich (?:so )?lieb", r"hab dich lieb"],
        "fa": [r"دوست(?:ت)? دارم", r"عاشقتم"],
    }),
    ("intent_imperative_volumeup", "works", "hub speech gain, 5 levels", {
        "en": [r"(?:volume up|turn (?:the volume |it )?up|louder|speak up|increase (?:the )?volume|be louder)"],
        "de": [r"lauter(?: bitte)?", r"(?:mach |dreh )?(?:die )?lautstärke (?:hoch|lauter|rauf)", r"sprich lauter"],
        "fa": [r"(?:صدا(?:ت|تو)? )?(?:رو |را )?(?:بلندتر|زیاد) کن", r"بلندتر(?: حرف بزن)?"],
    }),
    ("intent_imperative_volumedown", "works", "hub speech gain, 5 levels", {
        "en": [r"(?:volume down|turn (?:the volume |it )?down|quieter|softer|lower (?:the )?volume|decrease (?:the )?volume|be quieter)"],
        "de": [r"leiser(?: bitte)?", r"(?:mach |dreh )?(?:die )?lautstärke (?:runter|leiser)", r"sprich leiser"],
        "fa": [r"(?:صدا(?:ت|تو)? )?(?:رو |را )?(?:آرومتر|ارومتر|یواشتر|یواش تر|کم) کن", r"(?:آرومتر|ارومتر|یواشتر)(?: حرف بزن)?"],
    }),
    ("intent_imperative_volumelevel_extend", "works", "levels 1-5 / min / max", {
        "en": [r"(?:set (?:the )?)?volume (?:to |at |level )?(?P<n>\w+)"],
        "de": [r"lautstärke (?:auf )?(?P<n>\w+)"],
        "fa": [r"صدا (?:رو |را )?(?:روی |رو )?(?P<n>\w+) (?:بذار|بزار|کن)"],
    }),
    ("intent_imperative_shutup", "works", "stops actions; shutup clip; no speech", {
        "en": [r"shut up", r"be quiet", r"quiet", r"stop(?: it| that| talking| moving)?", r"hush", r"silence"],
        "de": [r"(?:sei |seid )?(?:still|ruhig|leise)", r"halt (?:die klappe|den mund)", r"ruhe", r"stopp?", r"hör auf"],
        "fa": [r"ساکت(?: شو| باش)?", r"بسه", r"وایسا", r"تمومش کن", r"خفه شو"],
    }),
    ("intent_system_sleep", "works", "sleeping face until spoken to or 'wake up'", {
        "en": [r"(?:go to sleep|go to bed|sleep|time to sleep|take a nap|nap)"],
        "de": [r"(?:geh |gehe )?schlafen", r"schlaf(?:e)?(?: jetzt)?", r"leg dich schlafen"],
        "fa": [r"(?:برو )?بخواب", r"وقت خوابه"],
    }),
    ("intent_system_wake", "works", "wake-up clip", {
        "en": [r"wake up", r"rise and shine"],
        "de": [r"wach auf", r"aufwachen"],
        "fa": [r"بیدار شو", r"پاشو"],
    }),
    ("intent_greeting_hello", "works", "hello clip + 'Hi!'", {
        "en": [r"(?:hello|hi|hey|hiya|howdy)(?: there| buddy| robot)?"],
        "de": [r"(?:hallo|hi|hey|servus|moin|grüß dich|guten tag)"],
        "fa": [r"سلام(?: علیکم| رفیق)?", r"درود"],
    }),
    ("intent_greeting_goodmorning", "works", "", {
        "en": [r"good morning", r"morning", r"good afternoon"],
        "de": [r"guten morgen", r"morgen", r"guten mittag"],
        "fa": [r"صبح بخیر", r"صبح به خیر"],
    }),
    ("intent_greeting_goodnight", "works", "", {
        "en": [r"good ?night", r"night night", r"sweet dreams"],
        "de": [r"gute nacht", r"schlaf gut"],
        "fa": [r"شب بخیر", r"شب به خیر", r"خوب بخوابی"],
    }),
    ("intent_greeting_goodbye", "works", "", {
        "en": [r"good ?bye", r"bye(?: bye)?", r"see you(?: later)?", r"see ya"],
        "de": [r"tschüss", r"tschüs", r"auf wiedersehen", r"bis später", r"ciao"],
        "fa": [r"خداحافظ", r"خدا نگهدار", r"بای"],
    }),
    ("intent_seasonal_happynewyear", "partial", "spoken + hello clip (no fireworks sprite)", {
        "en": [r"happy new year"], "de": [r"frohes neues(?: jahr)?", r"gutes neues jahr"], "fa": [r"سال نو مبارک", r"عید(?:ت)? مبارک"],
    }),
    ("intent_seasonal_happyholidays", "partial", "spoken + hello clip", {
        "en": [r"(?:happy holidays|merry christmas)"], "de": [r"frohe weihnachten", r"schöne feiertage"], "fa": [r"کریسمس مبارک"],
    }),
    ("intent_names_username_extend", "works", "enrolls the face under that name (one explicit NVIDIA check per photo)", {
        "en": [r"my name is (?P<n>[^\s].{0,30})", r"call me (?P<n>[^\s].{0,30})"],
        "de": [r"ich heiße (?P<n>[^\s].{0,30})", r"ich heisse (?P<n>[^\s].{0,30})", r"mein name ist (?P<n>[^\s].{0,30})", r"nenn mich (?P<n>[^\s].{0,30})"],
        "fa": [r"اسم من (?P<n>[^\s].{0,30}?) (?:است|ه|هست)", r"اسمم (?P<n>[^\s].{0,30}?) (?:است|ه|هست)", r"من (?P<n>[^\s].{0,30}?) هستم"],
    }),
    # cube / game / cloud features: honest "not yet"
    ("intent_play_rollcube", "not feasible yet", "cube needs BLE (masked)", {
        "en": [r"roll (?:your |the |a )?cube"], "de": [r"(?:roll|rolle) (?:deinen |den )?würfel"], "fa": [r"(?:مکعب|مکعبت)(?:و|رو)? (?:قل بده|بغلتون)"],
    }),
    ("intent_imperative_findcube", "not feasible yet", "cube needs BLE (masked)", {
        "en": [r"find (?:your |the )?cube"], "de": [r"(?:finde?|such) (?:deinen |den )?würfel"], "fa": [r"(?:مکعب|مکعبت)(?:و|رو)? پیدا کن"],
    }),
    ("intent_imperative_fetchcube", "not feasible yet", "cube needs BLE (masked)", {
        "en": [r"(?:fetch|bring me|get) (?:your |the )?cube", r"bring (?:me )?(?:your |the )?cube"], "de": [r"(?:bring|hol) (?:mir )?(?:deinen |den )?würfel"], "fa": [r"(?:مکعب|مکعبت)(?:و|رو)? (?:بیار|بیاور)"],
    }),
    ("intent_play_pickupcube", "not feasible yet", "cube needs BLE (masked)", {
        "en": [r"pick up (?:your |the )?cube"], "de": [r"heb (?:deinen |den )?würfel (?:hoch|auf)"], "fa": [r"(?:مکعب|مکعبت)(?:و|رو)? بردار"],
    }),
    ("intent_play_keepaway", "not feasible yet", "cube needs BLE (masked)", {
        "en": [r"(?:play )?keep ?away"], "de": [r"(?:spiel )?keep ?away"], "fa": [r"بازی (?:کیپ اوی|دور نگه دار)"],
    }),
    ("intent_play_popawheelie", "not feasible yet", "needs the cube", {
        "en": [r"(?:pop|do) a wheelie"], "de": [r"mach (?:einen |ein )?wheelie"], "fa": [r"(?:تک چرخ|ویلی) (?:بزن|برو)"],
    }),
    ("intent_play_blackjack", "not feasible yet", "game not ported", {
        "en": [r"(?:let's |lets )?play (?:blackjack|cards|a game)"], "de": [r"(?:lass uns )?(?:blackjack|karten) spielen", r"spiel(?:en wir)? blackjack"], "fa": [r"(?:بیا )?(?:بلک ?جک|ورق) بازی کنیم"],
    }),
    # SSH by voice only (owner's request, 10 Oct 2026). Disabling has an extra gate in
    # main.ssh_disable_ok: exact pattern, or router >= 0.85 + explicit off verb; else Vector asks.
    ("intent_system_ssh_enable", "works", "voice SSH on (robot listener on port 22); no face check", {
        "en": [r"(?:enable|turn on|switch on|open|start|activate) (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج)(?: access| server)?",
               r"turn (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) on", r"switch (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) on", r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) on"],
        "de": [r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:an|ein|einschalten|anschalten|aktivieren)", r"(?:schalt|schalte|mach|mache) (?:das )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:an|ein)",
               r"aktivier(?:e)? (?:das )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج)"],
        "fa": [r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:رو |را )?(?:روشن|فعال|باز) (?:کن|بکن)", r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:رو |را )?روشن"],
    }),
    ("intent_system_ssh_disable", "works", "voice SSH off; needs a clear match or a spoken yes", {
        "en": [r"(?:disable|turn off|switch off|close|stop|deactivate|shut off|shut down) (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج)(?: access| server)?",
               r"turn (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) off", r"switch (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) off", r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) off"],
        "de": [r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:aus|ausschalten|abschalten|deaktivieren)", r"(?:schalt|schalte|mach|mache) (?:das )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) aus",
               r"deaktivier(?:e)? (?:das )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج)"],
        "fa": [r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:رو |را )?(?:خاموش|غیرفعال|غیر فعال) (?:کن|بکن)", r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:رو |را )?ببند"],
    }),
    ("intent_system_ssh_status", "works", "says whether SSH is on (and shows the SSH face)", {
        "en": [r"is (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:on|off|up|down|enabled|disabled|open|running|active|working|available)", r"(?:what is |what's )?(?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) status",
               r"is (?:the )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:switched|turned) (?:on|off)"],
        "de": [r"ist (?:das )?(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:an|aus|aktiv|eingeschaltet|ausgeschaltet|offen)", r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) status", r"läuft (?:das )?(?:ssh|s s h|es es ha)"],
        "fa": [r"(?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج) (?:روشنه|روشن است|روشن هست|خاموشه|خاموش است|فعاله|فعال است)", r"وضعیت (?:ssh|s s h|es es ha|اس ?اس ?اچ|اس ?اس ?اج)"],
    }),
    ("intent_explore_stop", "works", "ends a wander session (stop / shut up also do)", {
        "en": [r"stop (?:exploring|wandering|driving around|driving)", r"(?:that's enough|enough) exploring", r"don't explore"],
        "de": [r"hör auf (?:zu erkunden|herumzufahren|zu fahren)", r"nicht mehr (?:erkunden|herumfahren)", r"erkunden (?:stopp|beenden|aus)"],
        "fa": [r"(?:دیگه )?(?:نگرد|گشت نزن)", r"گشتن (?:رو |را )?(?:تموم|بس) کن"],
    }),
    ("intent_explore_start", "works off the charger (needs explore.enabled)", "slow wander on the robot: cliff stop wins, obstacle <100 mm turn, 10 min cap", {
        "en": [r"(?:go |start |let's |lets )?explor(?:e|ing)(?: around| the room| a bit)?", r"(?:go |start )?(?:wander|drive|roam|look) around(?: a bit)?",
               r"go (?:on an )?adventure"],
        "de": [r"(?:geh |fang an zu )?erkunde[n]?(?: die gegend| den raum)?", r"(?:fahr|fahre) (?:herum|rum|umher)", r"geh auf (?:entdeckungstour|erkundung)"],
        "fa": [r"برو (?:بگرد|گشت بزن|بچرخ|اکتشاف کن)", r"اکتشاف کن", r"(?:یه )?دوری بزن"],
    }),
    ("intent_imperative_eyecolor", "not feasible yet", "eye colour not configurable yet", {
        "en": [r"(?:change|switch) (?:your )?eye colou?r(?: to \w+)?"], "de": [r"(?:änder|wechsel) (?:deine )?augenfarbe"], "fa": [r"رنگ چشم(?:ات|هات|ت)(?:و|رو)? عوض کن"],
    }),
    ("intent_message_recordmessage_extend", "not feasible yet", "messages not ported", {
        "en": [r"record (?:a )?message(?: for .*)?"], "de": [r"nimm (?:eine )?nachricht auf.*"], "fa": [r"(?:یه |یک )?پیام ضبط کن.*"],
    }),
    ("intent_message_playmessage_extend", "not feasible yet", "messages not ported", {
        "en": [r"play (?:my |the )?messages?"], "de": [r"spiel (?:meine |die )?nachrichten?(?: ab)?"], "fa": [r"پیام(?:ها|م)?(?:و|رو)? پخش کن"],
    }),
    ("intent_amazon_signin", "not feasible yet", "Alexa is not on this robot", {
        "en": [r"sign (?:in|out) (?:to |of )?alexa", r"alexa sign (?:in|out)"], "de": [r"(?:bei )?alexa (?:anmelden|abmelden)"], "fa": [r"الکسا"],
    }),
]

# Not intercepted (left to chat on purpose): weather (web search answers),
# names_ask (the face-ID identity path), knowledge questions, yes/no,
# blackjack hit/stand.
PASS_THROUGH = {
    "intent_weather_extend": "chat with web search answers it (no weather face)",
    "intent_names_ask": "identity question path (one NVIDIA face check, this turn only)",
    "intent_knowledge_promptquestion": "normal chat",
    "intent_imperative_affirmative": "normal chat",
    "intent_imperative_negative": "normal chat",
    "intent_system_noaudio": "dropped by VAD/STT already",
}

_COMPILED = [(n, s, note, {lg: [re.compile(p) for p in pats] for lg, pats in d.items()}) for n, s, note, d in _DEF]
STATUS = {n: (s, note) for n, s, note, _ in _DEF}
_IDENTITY_GUARD = re.compile(r"(?:what|who|chi|چی|چیه|چیست|کی|wie|wer)\b|\?$")


def match(text: str) -> Intent | None:
    t = normalise(text)
    if not t or len(t.split()) > 12:
        return None
    for name, _s, _n, langs in _COMPILED:
        for lang, pats in langs.items():
            for p in pats:
                m = p.fullmatch(t)
                if not m:
                    continue
                arg = (m.groupdict().get("n") or "").strip()
                if name == "intent_names_username_extend":
                    # "اسم من چیه" / "what's my name" are identity questions, never a name
                    if not arg or _IDENTITY_GUARD.search(arg) or arg in ("چی", "کی", "چیه"):
                        continue
                return Intent(name=name, lang=lang, arg=arg, text=t)
    return None


# ---------------------------------------------------------------- numbers
_WORDS = {
    "en": {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
           "forty five": 45, "fifty": 50, "sixty": 60, "half an": 0.5, "a half": 0.5},
    "de": {"ein": 1, "eine": 1, "einen": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
           "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "fünfzehn": 15, "zwanzig": 20,
           "dreißig": 30, "vierzig": 40, "fünfzig": 50, "sechzig": 60, "eine halbe": 0.5, "halbe": 0.5},
    "fa": {"یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "شیش": 6, "هفت": 7, "هشت": 8,
           "نه": 9, "ده": 10, "یازده": 11, "دوازده": 12, "پانزده": 15, "پونزده": 15, "بیست": 20, "سی": 30,
           "چهل": 40, "پنجاه": 50, "شصت": 60, "نیم": 0.5},
}
_UNITS = [
    (r"(?:hours?|stunden?|ساعت)", 3600),
    (r"(?:minutes?|mins?|minuten?|دقیقه)", 60),
    (r"(?:seconds?|secs?|sekunden?|ثانیه)", 1),
]


def parse_duration(s: str) -> int:
    """'5 minutes', 'two and a half minutes', 'zehn sekunden', '۳ دقیقه' -> seconds (0 if none)."""
    s = normalise(s)
    total = 0.0
    for unit_re, mult in _UNITS:
        m = re.search(r"((?:\d+(?:[.,]\d+)?|[^\d\s]+(?: [^\d\s]+)?))\s*(?:and a half |und eine halbe |و نیم )?" + unit_re, s)
        if not m:
            continue
        tok = m.group(1).strip()
        val = None
        if re.fullmatch(r"\d+(?:[.,]\d+)?", tok):
            val = float(tok.replace(",", "."))
        else:
            for lang in _WORDS.values():
                if tok in lang:
                    val = lang[tok]
                    break
                last = tok.split()[-1]
                if last in lang:
                    val = lang[last]
                    break
        if val is None:
            continue
        if re.search(r"(?:and a half|und eine halbe|و نیم)\s*" + unit_re, s):
            val += 0.5
        total += val * mult
    return int(round(total))


def parse_level(s: str) -> int:
    s = normalise(s)
    if s in ("max", "maximum", "full", "maximal", "voll", "زیاد", "حداکثر"):
        return 5
    if s in ("min", "minimum", "low", "lowest", "niedrig", "leise", "کم", "حداقل"):
        return 1
    if s in ("medium", "mittel", "normal", "متوسط"):
        return 3
    if s.isdigit():
        return max(1, min(5, int(s)))
    for lang in _WORDS.values():
        if s in lang and lang[s] >= 1:
            return max(1, min(5, int(lang[s])))
    return 0


# ---------------------------------------------------------------- replies
R = {
    "charger": {"en": "I can't find my charger by myself yet. Please put me on it.",
                "de": "Ich finde meine Ladestation noch nicht allein. Bitte setz mich drauf.",
                "fa": "هنوز نمی‌تونم خودم شارژرم رو پیدا کنم. لطفاً منو بذار روش."},
    "leave": {"en": "Okay, getting off my charger.", "de": "Okay, ich fahre runter.", "fa": "باشه، از شارژر میام پایین."},
    "on_charger": {"en": "I'm on my charger. Say 'get off the charger' first.",
                   "de": "Ich stehe auf der Ladestation. Sag zuerst: fahr von der Ladestation.",
                   "fa": "من روی شارژرم. اول بگو از شارژر بیا پایین."},
    "not_on_charger": {"en": "I'm not on my charger.", "de": "Ich bin nicht auf der Ladestation.", "fa": "من روی شارژر نیستم."},
    "drive_off": {"en": "Driving is switched off right now.", "de": "Fahren ist gerade ausgeschaltet.", "fa": "رانندگی الان خاموشه."},
    "come": {"en": "Coming!", "de": "Ich komme!", "fa": "اومدم!"},
    "fistbump": {"en": "Fist bump!", "de": "Faust!", "fa": "بزن قدش!"},
    "photo": {"en": "Got it. I took a photo.", "de": "Erledigt, ich habe ein Foto gemacht.", "fa": "عکس گرفتم."},
    "no_photo": {"en": "I can't see anything right now.", "de": "Ich sehe gerade nichts.", "fa": "الان چیزی نمی‌بینم."},
    "timer_how_long": {"en": "How long should the timer be?", "de": "Wie lange soll der Timer laufen?", "fa": "تایمر چقدر باشه؟"},
    "timer_set": {"en": "Timer set for {d}.", "de": "Timer auf {d} gestellt.", "fa": "تایمر برای {d} تنظیم شد."},
    "timer_left": {"en": "{d} left.", "de": "Noch {d}.", "fa": "{d} مونده."},
    "no_timer": {"en": "There's no timer running.", "de": "Es läuft kein Timer.", "fa": "تایمری فعال نیست."},
    "timer_cancel": {"en": "Timer cancelled.", "de": "Timer gestoppt.", "fa": "تایمر لغو شد."},
    "timer_done": {"en": "Your timer is done!", "de": "Dein Timer ist abgelaufen!", "fa": "تایمرت تموم شد!"},
    "time": {"en": "It's {t}.", "de": "Es ist {t} Uhr.", "fa": "ساعت {t} است."},
    "age": {"en": "I'm {d} old.", "de": "Ich bin {d} alt.", "fa": "من {d} سن دارم."},
    "age_unknown": {"en": "I don't know my birthday yet.", "de": "Ich kenne meinen Geburtstag noch nicht.", "fa": "هنوز تاریخ تولدم رو نمی‌دونم."},
    "volume": {"en": "Volume {n}.", "de": "Lautstärke {n}.", "fa": "صدا {n}."},
    "hello": {"en": "Hi!", "de": "Hallo!", "fa": "سلام!"},
    "goodmorning": {"en": "Good morning!", "de": "Guten Morgen!", "fa": "صبح بخیر!"},
    "goodnight": {"en": "Good night!", "de": "Gute Nacht!", "fa": "شب بخیر!"},
    "goodbye": {"en": "Bye!", "de": "Tschüss!", "fa": "خداحافظ!"},
    "newyear": {"en": "Happy New Year!", "de": "Frohes neues Jahr!", "fa": "سال نو مبارک!"},
    "holidays": {"en": "Happy holidays!", "de": "Frohe Feiertage!", "fa": "تعطیلات مبارک!"},
    "enrolled": {"en": "Nice to meet you, {n}! I'll remember your face.",
                 "de": "Schön, dich kennenzulernen, {n}! Ich merke mir dein Gesicht.",
                 "fa": "از آشنایی‌ات خوشحالم {n}! چهره‌ات رو یادم می‌مونه."},
    "enroll_fail": {"en": "Nice to meet you, {n}. I couldn't see your face, please look at me and say it again.",
                    "de": "Hallo {n}. Ich konnte dein Gesicht nicht sehen, schau mich an und sag es nochmal.",
                    "fa": "سلام {n}. صورتت رو ندیدم، به من نگاه کن و دوباره بگو."},
    "explore": {"en": "Okay, exploring! Say stop to stop me.", "de": "Okay, ich erkunde! Sag stopp, um mich anzuhalten.",
                "fa": "باشه، می‌رم بگردم! بگو وایسا تا وایسم."},
    "explore_off": {"en": "Exploring is switched off on me right now.", "de": "Erkunden ist bei mir gerade ausgeschaltet.",
                    "fa": "گشتن الان روی من خاموشه."},
    "ssh_on": {"en": "SSH is on.", "de": "SSH ist an.", "fa": "اس اس اچ روشنه."},
    "ssh_off": {"en": "SSH is off.", "de": "SSH ist aus.", "fa": "اس اس اچ خاموشه."},
    "ssh_confirm": {"en": "Did you want me to turn SSH off? Say yes.", "de": "Soll ich SSH ausschalten? Sag ja.",
                    "fa": "می‌خوای اس اس اچ رو خاموش کنم؟ بگو آره."},
    "ssh_cancel": {"en": "Okay, SSH stays on.", "de": "Okay, SSH bleibt an.", "fa": "باشه، اس اس اچ روشن می‌مونه."},
    "ssh_fail": {"en": "I couldn't change SSH.", "de": "Ich konnte SSH nicht umschalten.", "fa": "نتونستم اس اس اچ رو عوض کنم."},
    "ssh_unknown": {"en": "I can't tell right now.", "de": "Das weiß ich gerade nicht.", "fa": "الان نمی‌دونم."},
    "session_stop": {"en": "Okay, I'll stop listening.", "de": "Okay, ich höre nicht mehr zu.",
                     "fa": "باشه، دیگه گوش نمی‌دم."},
    "explore_stop": {"en": "Okay, I'll stop.", "de": "Okay, ich halte an.", "fa": "باشه، وایمیستم."},
    "cube": {"en": "I can't play with my cube yet.", "de": "Mit meinem Würfel kann ich noch nicht spielen.", "fa": "هنوز نمی‌تونم با مکعبم بازی کنم."},
    "cant": {"en": "Sorry, I can't do that yet.", "de": "Das kann ich leider noch nicht.", "fa": "متأسفم، هنوز این کار رو بلد نیستم."},
}


def say(key: str, lang: str, **kw) -> str:
    d = R[key]
    return d.get(lang, d["en"]).format(**kw)


def fmt_duration(sec: int, lang: str) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    names = {"en": ("hour", "hours", "minute", "minutes", "second", "seconds", " and "),
             "de": ("Stunde", "Stunden", "Minute", "Minuten", "Sekunde", "Sekunden", " und "),
             "fa": ("ساعت", "ساعت", "دقیقه", "دقیقه", "ثانیه", "ثانیه", " و ")}[lang if lang in ("en", "de", "fa") else "en"]
    parts = []
    for v, one, many in ((h, names[0], names[1]), (m, names[2], names[3]), (s, names[4], names[5])):
        if v:
            parts.append(f"{v} {one if v == 1 else many}")
    return names[6].join(parts) if parts else f"0 {names[5]}"


def fmt_age(days: int, lang: str) -> str:
    y, rem = divmod(max(0, days), 365)
    mo = rem // 30
    w = {"en": ("year", "years", "month", "months", "day", "days", " and "),
         "de": ("Jahr", "Jahre", "Monat", "Monate", "Tag", "Tage", " und "),
         "fa": ("سال", "سال", "ماه", "ماه", "روز", "روز", " و ")}[lang if lang in ("en", "de", "fa") else "en"]
    if y == 0 and mo == 0:
        return f"{days} {w[4] if days == 1 else w[5]}"
    parts = []
    if y:
        parts.append(f"{y} {w[0] if y == 1 else w[1]}")
    if mo:
        parts.append(f"{mo} {w[2] if mo == 1 else w[3]}")
    return w[6].join(parts)


# intent -> (face clip shown with the reply, robot action, reply key)
SIMPLE = {
    "intent_imperative_praise": ("goodrobot", "", ""),
    "intent_imperative_abuse": ("badrobot", "", ""),
    "intent_imperative_apologize": ("apology", "", ""),
    "intent_imperative_love": ("iloveyou", "", ""),
    "intent_system_wake": ("wakeup", "", ""),
    "intent_greeting_hello": ("hello", "", "hello"),
    "intent_greeting_goodmorning": ("goodmorning", "", "goodmorning"),
    "intent_greeting_goodnight": ("goodnight", "", "goodnight"),
    "intent_greeting_goodbye": ("goodbye", "", "goodbye"),
    "intent_seasonal_happynewyear": ("hello", "", "newyear"),
    "intent_seasonal_happyholidays": ("hello", "", "holidays"),
    "intent_imperative_lookatme": ("", "look_at_me", ""),
    "intent_imperative_dance": ("", "dance", ""),
    "intent_play_anytrick": ("", "dance", ""),
    "intent_play_fistbump": ("", "fistbump", "fistbump"),
    "intent_system_charger": ("cant_help", "", "charger"),
}
DRIVE = {
    "intent_imperative_come": ("come_here", "come"),
    "intent_imperative_forward": ("forward", ""),
    "intent_imperative_backup": ("backup", ""),
    "intent_imperative_turnleft": ("turn_left", ""),
    "intent_imperative_turnright": ("turn_right", ""),
    "intent_imperative_turnaround": ("turn_around", ""),
}
CUBE = {"intent_play_rollcube", "intent_imperative_findcube", "intent_imperative_fetchcube",
        "intent_play_pickupcube", "intent_play_keepaway", "intent_play_popawheelie"}
CANT = {"intent_play_blackjack", "intent_imperative_eyecolor",
        "intent_message_recordmessage_extend", "intent_message_playmessage_extend", "intent_amazon_signin"}


def status_table() -> list[dict]:
    rows = [{"intent": n, "status": s, "note": note} for n, (s, note) in STATUS.items()]
    rows += [{"intent": n, "status": "chat", "note": note} for n, note in PASS_THROUGH.items()]
    return rows


# ---------------------------------------------------------------- SSH by voice
SSH_WORD = re.compile(r"(?:\bssh\b|\bs s h\b|\bes es ha\b|اس ?اس ?اچ|اس ?اس ?اج)")
_SSH_OFF_VERB = re.compile(
    r"(?:\bdisable\b|\bdeactivate\b|\b(?:turn|switch|shut)\b(?: \w+){0,2} (?:off|down)\b|\bclose\b|"
    r"ausschalten|abschalten|deaktivier|\b(?:schalt|schalte|mach|mache)\b.*\baus\b|"
    r"خاموش (?:کن|بکن)|غیر ?فعال (?:کن|بکن)|ببند)"
)
_YES = re.compile(r"(?:yes|yeah|yep|yup|sure|do it|correct|yes please|ja|jawohl|genau|ja bitte|mach das|بله|آره|اره|آری|باشه|اوهوم)")


def ssh_off_explicit(text: str) -> bool:
    """The transcript itself names SSH and an off verb (not just 'SSH is off ...')."""
    t = normalise(text)
    return bool(SSH_WORD.search(t) and _SSH_OFF_VERB.search(t))


def is_yes(text: str) -> bool:
    t = normalise(text)
    return bool(t) and len(t.split()) <= 4 and bool(_YES.fullmatch(t) or _YES.match(t) and len(t.split()) <= 2)
