from aiogram.utils.text_decorations import html_decoration as hd

from bot.models.prompt import I2VPrompt, Pair

START_GREETING = "Воспользуйтесь командами."

UNKNOWN_COMMAND = "Неизвестная команда."

CHOOSE_MODEL_TITLE = "Выберите модель:"

MODEL_IN_DEVELOPMENT = "Модель в разработке."

SEND_I2V_DOCUMENT = "Пришлите бриф промпта файлом .txt."

PARSING_IN_PROGRESS = "Обработка…"


def prompt_saved(title: str) -> str:
    return f"Готово: {hd.bold(hd.quote(title))}"


def prompts_saved_batch(titles: list[str]) -> str:
    lines = [f"Сохранено промптов: {len(titles)}"]
    lines += [f"— {hd.quote(title)}" for title in titles]
    return "\n".join(lines)


def album_file_failed(file_name: str) -> str:
    return f"Не удалось обработать {hd.quote(file_name)}, пропущен."


PROMPTS_LIST_TITLE = "Сохранённые промпты:"

PROMPTS_LIST_EMPTY = "Нет сохранённых промптов. /add_prompt — добавить."


PROMPT_NOT_FOUND = "Промпт не найден."

ASK_NEW_TITLE = "Пришлите новое название промпта."


def prompt_deleted(title: str) -> str:
    # Plain text: this goes into a callback answer toast, which doesn't render HTML.
    return f"Удалено: {title}"


def prompt_renamed(old_title: str, new_title: str) -> str:
    return f"{hd.bold(hd.quote(old_title))} → {hd.bold(hd.quote(new_title))}"

I2V_PROMPTS_LIST_TITLE = "Выберите промпт:"

I2V_PROMPTS_LIST_EMPTY = "Нет сохранённых i2v-промптов. /add_prompt — добавить."

CHOOSE_GENERATION_MODEL_TITLE = "Выберите модель для генерации:"

CHOOSE_PAIR_COUNT_TITLE = "Сколько вариаций кадра на параграф использовать:"

SEND_SCENARIO_DOCUMENT = "Пришлите сценарий файлом .docx или .txt."

SCENARIO_EMPTY = "В сценарии не найдено ни одного параграфа."


def scenario_progress(done: int, total: int) -> str:
    return f"Обработка сценария: {done}/{total}"


_TELEGRAM_MESSAGE_LIMIT = 4096


def _render_pair(pair: Pair) -> str:
    block = [f"\n{pair.number}. {hd.bold(hd.quote(pair.title or pair.id))}"]
    if pair.intent:
        block.append(hd.quote(pair.intent))
    details = ", ".join(
        f"{label}: {hd.quote(value)}"
        for label, value in (
            ("frame", pair.frame),
            ("people", pair.people),
            ("camera", pair.camera),
        )
        if value
    )
    if details:
        block.append(hd.italic(details))
    return "\n".join(block)


def prompt_details(data: I2VPrompt) -> str:
    header_lines = [f"{hd.bold(hd.quote(data.title))}"]
    if data.sub_periods:
        periods = ", ".join(hd.quote(p.title) for p in data.sub_periods if p.title)
        header_lines.append(f"{hd.italic('Периоды')}: {periods}")
    if data.lore:
        header_lines.append(f"\n{hd.quote(data.lore)}")
    if data.pairs:
        header_lines.append(f"\n{hd.bold('Пары')} ({len(data.pairs)}):")
    header = "\n".join(header_lines)

    # Add pairs one at a time, stopping before the message would exceed
    # Telegram's limit — cutting mid-pair would break HTML tag balance.
    text = header
    for pair in data.pairs:
        block = _render_pair(pair)
        if len(text) + len(block) + 1 > _TELEGRAM_MESSAGE_LIMIT:
            text += "\n…"
            break
        text += "\n" + block
    return text
