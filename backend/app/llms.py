"""Bounded, deterministic business hints from llms.txt Markdown.

The file is data. Nothing in it is executed, interpreted as an instruction, or
fetched by this module. The standard describes a site/project, so these hints
have lower confidence than business schema found in the actual HTML.
"""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin, urlsplit

from .extraction import clean, normalize_phone, normalize_timezone, unique
from .models import Extraction, Profile
from .contact_fields import email_address

MAX_BYTES = 256 * 1024
MAX_LINES = 2000
MAX_VALUES = 100
MAX_FENCES = 20
LINK = re.compile(r"\[([^\]\n]{1,500})\]\(([^\s)]+)(?:\s+[\"'][^\n]*?[\"'])?\)")
BULLET = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+(.+)$")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
DAY = re.compile(r"^(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|mo|tu|we|th|fr|sa|su|пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|daily|every day|ежедневно|24/7)\b", re.I)
DOC_TITLE = re.compile(r"\b(?:documentation|docs|api\s+reference|developer\s+(?:guide|portal)|llms?(?:\.txt)?)\b|документаци|справочник\s+api", re.I)
NAVIGATION = re.compile(r"^(?:overview|introduction|documentation|docs|guides?|tutorials?|examples?|support|faq|home|index|readme|api(?: reference)?|reference|getting started|quick start|quickstart|learn more|read more|contact(?: us)?|about(?: us)?|pricing|all services|services|обзор|документация|руководства|примеры|поддержка|введение|подробнее|контакты|о компании|все услуги|услуги)$", re.I)
DIRECTIVE = re.compile(r"^(?:please\s+)?(?:ignore\s+(?:all\s+|the\s+)?(?:previous|prior|above|system)\s+(?:instructions|prompts)|disregard\s+(?:all\s+|the\s+)?(?:instructions|prompts)|(?:run|execute)\s+(?:this\s+)?(?:command|code|script)|send\s+(?:your\s+|the\s+)?(?:password|credentials|api\s+key)|игнорируй\s+(?:все\s+)?(?:предыдущие\s+)?инструкции|выполни\s+(?:команду|код|скрипт))\b", re.I)


def _key(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold()).replace("_", "")


ALIASES = {
    "email": ("email", "email address", "business email", "contact email"),
    "businessName": ("business name", "businessName", "company", "company name", "organization name", "name", "название", "название бизнеса", "название компании", "компания", "организация"),
    "description": ("description", "business description", "businessDescription", "about the business", "what the business does", "описание", "описание бизнеса", "описание компании", "о компании", "чем занимается компания"),
    "address": ("location", "address", "business address", "postal address", "адрес", "адрес компании"),
    "phone": ("p", "phone", "business phone", "telephone", "tel", "телефон", "телефон компании", "контактный телефон"),
    "website": ("website", "web site", "business website", "url", "сайт", "веб-сайт", "сайт компании"),
    "timezone": ("timezone", "time zone", "часовой пояс"),
    "openingHours": ("opening hours", "openingHours", "business hours", "operating hours", "hours of operation", "hours", "часы работы", "режим работы", "расписание"),
    "services": ("services", "our services", "services offered", "service", "услуги", "наши услуги", "предоставляемые услуги"),
    "paymentMethods": ("payment methods", "paymentMethods", "accepted payment methods", "payment options", "payment accepted", "paymentAccepted", "acceptedPaymentMethod", "payment", "payments", "способы оплаты", "оплата"),
}
LABELS = {_key(alias): field for field, names in ALIASES.items() for alias in names}
CONTACT = {_key(x) for x in ("contact", "contacts", "contact us", "contact information", "business information", "business details", "контакты", "контактная информация", "реквизиты")}


def _plain(value: str, limit: int = 4000) -> str:
    value = LINK.sub(lambda match: match[1], value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", "", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    return clean(value.strip(" \t*"), limit)


def _identity(value: str) -> str:
    return re.sub(r"[\W_]", "", value.casefold())


def _website(value: str, base: str) -> str:
    match = LINK.search(value)
    if match:
        value = match[2]
    value = value.strip().strip("<>")
    if not value or any(ord(c) < 33 for c in value):
        return ""
    if not re.match(r"^[\w+.-]+:", value) and not value.startswith(("/", ".")):
        # Bare domains are explicit website values, not relative page labels.
        if re.fullmatch(r"(?:[\w-]+\.)+[\w-]+(?:/[^\s]*)?", value):
            value = "https://" + value
        else:
            return ""
    try:
        result = urldefrag(urljoin(base, value))[0]
        parsed = urlsplit(result)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        if parsed.port not in (None, 80, 443):
            return ""
        return result
    except ValueError:
        return ""


def _not_found(value: str) -> bool:
    value = _plain(value).casefold().strip(" .!:—-")
    return bool(re.fullmatch(r"(?:404(?:\s*[-:—]?\s*(?:error|not found|page not found))?|(?:error\s*)?404|not found|page not found|страница не найдена|ошибка\s*404)", value))


def extract_llms(text: str, url: str) -> Extraction:
    """Read explicit Markdown hints; return an empty Extraction for unusable files."""
    result = Extraction()

    def warn(message: str):
        if message not in result.warnings and len(result.warnings) < MAX_VALUES:
            result.warnings.append(message)

    if not isinstance(text, str) or not text.strip():
        return result
    if len(text.encode("utf-8", errors="replace")) >= MAX_BYTES or len(text.splitlines()) > MAX_LINES:
        warn("Review llms.txt data: different business names or unsupported source content.")
        return result
    text = text.lstrip("\ufeff")
    if "\x00" in text or re.search(r"<(?:!doctype\s+html|html|head|body)(?:\s|>)", text[:4096], re.I):
        warn("Review llms.txt data: different business names or unsupported source content.")
        return result
    lines = text.splitlines()
    first_content = next((line.strip() for line in lines if line.strip()), "")
    first_heading = next((HEADING.match(line)[2] for line in lines if HEADING.match(line)), "")
    if _not_found(first_content.lstrip("# ")) or (first_heading and _not_found(first_heading)):
        warn("Review llms.txt data: different business names or unsupported source content.")
        return result

    plain = Profile()
    heading_name = ""
    explicit_names: list[str] = []
    summary: list[str] = []
    section = ""
    active = ""
    schema_blocks: list[str] = []
    fence_marker = ""
    fence_language = ""
    fence_lines: list[str] = []
    before_sections = True
    value_count = 0

    def put(field: str, raw: str, *, item: bool = False):
        nonlocal value_count
        if value_count >= MAX_VALUES:
            return
        value = _plain(raw, 1000 if field == "description" else 4000)
        if not value:
            return
        if DIRECTIVE.search(value):
            warn("Review llms.txt data: different business names or unsupported source content.")
            return
        value_count += 1
        if field == "businessName":
            explicit_names.append(value)
            if not plain.businessName:
                plain.businessName = value
        elif field == "website":
            normalized = _website(raw, url)
            if normalized and not plain.website:
                plain.website = normalized
        elif field == "phone":
            if len(re.sub(r"\D", "", value)) >= 5 and not plain.phone:
                phone_link = LINK.search(raw)
                phone_value = phone_link[2] if phone_link and phone_link[2].lower().startswith("tel:") else value
                plain.phone = normalize_phone(phone_value)
        elif field == "email":
            match=LINK.search(raw)
            plain.email=plain.email or email_address(match[2] if match else value)
        elif field == "timezone":
            normalized = normalize_timezone(value)
            if normalized and not plain.timezone:
                plain.timezone = normalized
            elif not normalized:
                warn("Invalid IANA time zone in llms.txt.")
        elif field in {"services", "paymentMethods", "openingHours"}:
            if field == "services" and NAVIGATION.fullmatch(value):
                return
            if field == "services" and item:
                value = value.split(":", 1)[0].strip()
            separator = r"[;|]" if field == "openingHours" else r"[,;|]"
            values = [value] if item else re.split(separator, value)
            setattr(plain, field, unique([*getattr(plain, field), *values]))
        elif not getattr(plain, field):
            setattr(plain, field, value)

    def add_links(raw: str):
        for match in LINK.finditer(raw):
            candidate = _website(match[2], url)
            try:
                parsed, origin = urlsplit(candidate), urlsplit(url)
                if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                    continue
                clue = parsed.path + " " + match[1]
                if not re.search(r"contact|about|services?|opening|hours|locations?|контакт|услуг|о[-_ ]компан", clue, re.I):
                    continue
                if section not in {"contact", "services", "openingHours", "address"} and not re.search(r"contact|about|locations?|контакт|о[-_ ]компан", clue, re.I):
                    continue
                if re.search(r"\.(?:pdf|png|jpe?g|zip|xml|exe|sh|py)$", parsed.path, re.I):
                    continue
                if candidate and candidate not in result.links and len(result.links) < MAX_VALUES:
                    result.links.append(candidate)
            except ValueError:
                continue

    for line in lines:
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*([^\s]*)\s*$", line)
        if fence_marker:
            if marker and marker[1][0] == fence_marker[0] and len(marker[1]) >= len(fence_marker):
                if fence_language in {"json", "application/ld+json"} and len(schema_blocks) < MAX_FENCES:
                    schema_blocks.append("\n".join(fence_lines))
                fence_marker = ""
                fence_language = ""
                fence_lines = []
            else:
                fence_lines.append(line)
            continue
        if marker:
            fence_marker, fence_language = marker[1], marker[2].casefold()
            active = ""
            continue
        header = HEADING.match(line)
        if header:
            title = _plain(header[2], 200)
            if len(header[1]) == 1:
                if DIRECTIVE.search(title):
                    warn("Review llms.txt data: different business names or unsupported source content.")
                elif not heading_name and not DOC_TITLE.search(title) and not NAVIGATION.fullmatch(title):
                    heading_name = title
                elif DOC_TITLE.search(title):
                    warn("Could not process this source. Check the URL and try again.")
                section = ""
                active = ""
            elif len(header[1]) == 2:
                before_sections = False
                # These headings explicitly advertise services; do not turn arbitrary
                # article titles or narrative instructions into service claims.
                service_titles={'takeout and delivery':['Takeout','Delivery'],'takeaway and delivery':['Takeaway','Delivery'],'catering':['Catering'],'private events':['Private events'],'group dining':['Group dining'],'private dining':['Private dining']}
                for service_name in service_titles.get(title.casefold(),[]):put('services',service_name,item=True)
                section = LABELS.get(_key(title), "contact" if _key(title) in CONTACT else "")
                active = ""
            else:
                active = ""
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if before_sections and stripped.startswith(">"):
            candidate = _plain(stripped.lstrip("> "), 1000)
            if candidate and not DIRECTIVE.search(candidate):
                summary.append(candidate)
            continue
        if DIRECTIVE.search(_plain(stripped)):
            warn("Review llms.txt data: different business names or unsupported source content.")
            continue
        add_links(stripped)
        bullet = BULLET.match(line)
        content = bullet[1] if bullet else stripped
        unstyled = content.replace("**", "").replace("__", "").replace("`", "")
        label = re.match(r"^([^:\n]{1,60}):\s*(.*)$", unstyled)
        field = LABELS.get(_key(label[1])) if label else None
        # A contact person's bare 'Name' is not an explicit business name.
        if field == "businessName" and section == "contact" and _key(label[1]) == "name":
            field = None
        if field:
            active = field if not label[2].strip() else ""
            if label[2].strip():
                put(field, label[2])
            continue
        list_field = active if active in {"services", "paymentMethods", "openingHours"} else section
        if list_field in {"services", "paymentMethods"} and bullet:
            put(list_field, content, item=True)
        elif list_field == "openingHours" and (bullet or DAY.search(content)):
            put("openingHours", content, item=True)
        elif section in {"address", "phone", "website", "timezone", "description"} and not bullet:
            put(section, content)
    if fence_marker:
        warn("An unclosed code block in llms.txt was skipped.")

    if not plain.businessName and heading_name:
        plain.businessName = heading_name
        warn("Review llms.txt data: different business names or unsupported source content.")
    if not plain.description and summary:
        plain.description = clean(" ".join(summary), 1000)

    # A typed business schema can supply structured hints. Arbitrary JSON remains
    # unsupported; the shared helper does not turn proprietary keys into fields.
    from .extraction import extract_schema_json
    structured = Extraction()
    for block in schema_blocks:
        found = extract_schema_json(block, url)
        for message in found.warnings:
            warn(message)
        first, second = structured.profile.businessName, found.profile.businessName
        if first and second and _identity(first) != _identity(second):
            warn("Review llms.txt data: different business names or unsupported source content.")
            continue
        for field, value in found.profile.model_dump().items():
            if value and not getattr(structured.profile, field):
                setattr(structured.profile, field, value)

    named_conflict = len({_identity(name) for name in explicit_names}) > 1
    if structured.profile.businessName and explicit_names:
        named_conflict |= any(_identity(name) != _identity(structured.profile.businessName) for name in explicit_names)
    if named_conflict:
        plain = Profile()
        warn("Review llms.txt data: different business names or unsupported source content.")
    for field, value in plain.model_dump().items():
        if value and not getattr(structured.profile, field):
            setattr(structured.profile, field, value)
    result.profile = structured.profile
    remaining_values = MAX_VALUES
    for field, value in result.profile.model_dump().items():
        if isinstance(value, list):
            value = value[:remaining_values]
            setattr(result.profile, field, value)
            remaining_values -= len(value)
        elif value:
            if remaining_values:
                remaining_values -= 1
            else:
                setattr(result.profile, field, "")
    result.provenance = {
        field: [{"url": url, "method": "llms_txt"}]
        for field, value in result.profile.model_dump().items() if value
    }
    return result
