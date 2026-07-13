from __future__ import annotations

"""Open the user's default e-mail client with an optional file attachment.

On Windows we use *Simple MAPI* (``MAPISendMail`` in ``mapi32.dll``) which is
the only standard way to pre-attach a file to a freshly composed message in the
system default mail client (Outlook, Thunderbird, Windows Mail, eM Client, ...).
Plain ``mailto:`` links cannot carry attachments, so we fall back to ``mailto:``
(plus revealing the saved PDF in Explorer) only when MAPI is unavailable.
"""

import logging
import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path

_logger = logging.getLogger(__name__)


class MailError(Exception):
    """Raised when no e-mail path (MAPI or mailto) could be used."""


def _send_via_mapi(
    subject: str,
    body: str,
    attachment: str | None,
    to: str | None,
) -> bool:
    """Try Simple MAPI. Returns True on success, False if MAPI is unusable.

    Raises MailError only for a genuine MAPI failure the caller should surface.
    """
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import POINTER, Structure, byref, c_char_p, c_ulong, c_void_p

    try:
        mapi = ctypes.WinDLL("mapi32.dll")
    except OSError:
        _logger.info("mapi32.dll not available — falling back to mailto")
        return False

    ULONG = c_ulong
    LPSTR = c_char_p
    LHANDLE = c_ulong

    class MapiRecipDesc(Structure):
        _fields_ = [
            ("ulReserved", ULONG),
            ("ulRecipClass", ULONG),
            ("lpszName", LPSTR),
            ("lpszAddress", LPSTR),
            ("ulEIDSize", ULONG),
            ("lpEntryID", c_void_p),
        ]

    class MapiFileDesc(Structure):
        _fields_ = [
            ("ulReserved", ULONG),
            ("flFlags", ULONG),
            ("nPosition", ULONG),
            ("lpszPathName", LPSTR),
            ("lpszFileName", LPSTR),
            ("lpFileType", c_void_p),
        ]

    class MapiMessage(Structure):
        _fields_ = [
            ("ulReserved", ULONG),
            ("lpszSubject", LPSTR),
            ("lpszNoteText", LPSTR),
            ("lpszMessageType", LPSTR),
            ("lpszDateReceived", LPSTR),
            ("lpszConversationID", LPSTR),
            ("flFlags", ULONG),
            ("lpOriginator", POINTER(MapiRecipDesc)),
            ("nRecipCount", ULONG),
            ("lpRecips", POINTER(MapiRecipDesc)),
            ("nFileCount", ULONG),
            ("lpFiles", POINTER(MapiFileDesc)),
        ]

    MAPI_LOGON_UI = 0x00000001
    MAPI_DIALOG = 0x00000008
    MAPI_TO = 1
    SUCCESS_SUCCESS = 0
    MAPI_E_USER_ABORT = 1

    def enc(value: str | None) -> bytes | None:
        if value is None:
            return None
        # Simple MAPI is ANSI; encode in the system codepage, drop unmappable chars.
        return value.encode("mbcs", errors="replace")

    message = MapiMessage()
    message.lpszSubject = enc(subject)
    message.lpszNoteText = enc(body or "")

    recips = None
    if to:
        recip = MapiRecipDesc()
        recip.ulRecipClass = MAPI_TO
        recip.lpszName = enc(to)
        recip.lpszAddress = enc(f"SMTP:{to}")
        recips = (MapiRecipDesc * 1)(recip)
        message.nRecipCount = 1
        message.lpRecips = recips

    files = None
    if attachment:
        path = Path(attachment)
        file_desc = MapiFileDesc()
        file_desc.flFlags = 0
        file_desc.nPosition = 0xFFFFFFFF  # let the client place the icon at the end
        file_desc.lpszPathName = enc(str(path))
        file_desc.lpszFileName = enc(path.name)
        files = (MapiFileDesc * 1)(file_desc)
        message.nFileCount = 1
        message.lpFiles = files

    MAPISendMail = mapi.MAPISendMail
    MAPISendMail.restype = ULONG
    MAPISendMail.argtypes = [LHANDLE, c_void_p, POINTER(MapiMessage), ULONG, ULONG]

    result = MAPISendMail(0, 0, byref(message), MAPI_LOGON_UI | MAPI_DIALOG, 0)
    if result in (SUCCESS_SUCCESS, MAPI_E_USER_ABORT):
        # USER_ABORT just means the operator closed the compose window — the
        # client opened fine, which is all we promise to do.
        return True
    raise MailError(f"MAPISendMail zwrócił błąd {result}.")


def _send_via_mailto(subject: str, body: str, to: str | None) -> None:
    query = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    webbrowser.open(f"mailto:{to or ''}?{query}")


def _reveal_in_explorer(path: str) -> None:
    if sys.platform != "win32":
        return
    try:
        os.startfile(str(Path(path).parent))  # type: ignore[attr-defined]
    except OSError:
        pass



def send_via_smtp(subject: str, body: str, attachment: str | None, config: dict) -> None:
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = config.get('user')
    msg['To'] = config.get('to')
    msg.set_content(body)

    if attachment:
        import mimetypes
        path = Path(attachment)
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = 'application/octet-stream'
        maintype, subtype = ctype.split('/', 1)
        with open(path, 'rb') as fp:
            msg.add_attachment(fp.read(), maintype=maintype, subtype=subtype, filename=path.name)

    host = config.get('host', '')
    port = int(config.get('port', 465))
    user = config.get('user', '')
    password = config.get('password', '')
    use_ssl = config.get('ssl', True)

    if use_ssl:
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)

def compose_email(

    subject: str,
    body: str = "",
    attachment: str | None = None,
    to: str | None = None,
) -> str:
    """Open the default mail client. Returns the path actually used.

    * ``"mapi"``    — the file was pre-attached via Simple MAPI.
    * ``"mailto"``  — opened a plain compose window; the attachment (if any) was
      revealed in Explorer so the user can drag it in manually.
    """
    try:
        if _send_via_mapi(subject, body, attachment, to):
            return "mapi"
    except MailError as exc:
        _logger.warning("MAPI send failed, falling back to mailto: %s", exc)

    note = body
    if attachment:
        note = (body + "\n\n" if body else "") + (
            f"[Załącznik PDF zapisano w: {attachment} — przeciągnij plik do wiadomości.]"
        )
    _send_via_mailto(subject, note, to)
    if attachment:
        _reveal_in_explorer(attachment)
    return "mailto"
