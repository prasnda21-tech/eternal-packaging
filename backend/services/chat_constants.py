"""Internal work chat constants and file validation policy."""
from __future__ import annotations

ROOM_DM = "dm"
ROOM_SYSTEM_GROUP = "system_group"
ROOM_CUSTOM_GROUP = "custom_group"
ROOM_WORK = "work"

PERM_CHAT_USE = "chat.use"
PERM_CHAT_GROUP_MANAGE = "chat.group.manage"
PERM_CHAT_MODERATE = "chat.moderate"
NAV_CHAT = "nav.chat"

COLL_ROOMS = "chat_rooms"
COLL_MEMBERS = "chat_members"
COLL_MESSAGES = "chat_messages"
COLL_ATTACHMENTS = "chat_attachments"

EDIT_WINDOW_SECONDS = 15 * 60
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5
MESSAGE_PAGE_DEFAULT = 40

ALLOWED_IMAGE_MIME = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
})

ALLOWED_DOC_MIME = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
})

ALLOWED_MIME = ALLOWED_IMAGE_MIME | ALLOWED_DOC_MIME

EXTENSION_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

DANGEROUS_EXTENSIONS = frozenset({"exe", "bat", "cmd", "ps1", "js", "msi", "com", "scr", "vbs"})
DANGEROUS_MAGIC = (b"MZ", b"\x7fELF")

CHAT_FILE_KIND = "chat_attachment"
CHAT_FILE_SOURCE_TYPE = "CHAT_ATTACHMENT"
