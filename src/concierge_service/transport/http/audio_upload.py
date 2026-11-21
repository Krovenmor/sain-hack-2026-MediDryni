"""Read one multipart audio file within the proxy's upload limit."""

from pathlib import PurePath

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

MAX_AUDIO_BYTES = 5_000_000
_MAX_MULTIPART_BYTES = MAX_AUDIO_BYTES + 8192
_AUDIO_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".webm": "audio/webm",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac",
}


class AudioTooLarge(MultiPartException):
    pass


async def read_audio(request: Request) -> tuple[bytes, str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, detail={"code": "audio_multipart_required"})

    async def bounded_stream():
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > _MAX_MULTIPART_BYTES:
                raise AudioTooLarge("Audio upload exceeds the allowed size")
            yield chunk

    try:
        form = await MultiPartParser(request.headers, bounded_stream(), max_files=1, max_fields=0).parse()
    except AudioTooLarge:
        raise HTTPException(413, detail={"code": "audio_too_large"}) from None
    except MultiPartException:
        raise HTTPException(422, detail={"code": "invalid_audio"}) from None

    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(422, detail={"code": "invalid_audio"})
        extension = PurePath(upload.filename or "").suffix.lower()
        if extension not in _AUDIO_TYPES:
            raise HTTPException(415, detail={"code": "unsupported_audio"})
        audio = await upload.read(MAX_AUDIO_BYTES + 1)
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, detail={"code": "audio_too_large"})
        if not audio:
            raise HTTPException(422, detail={"code": "invalid_audio"})
        return audio, "recording" + extension, _AUDIO_TYPES[extension]
    finally:
        await form.close()
