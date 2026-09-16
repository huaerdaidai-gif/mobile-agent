# -*- coding: utf-8 -*-
"""音频预处理：任意音频转成 16 kHz / mono / pcm_s16le WAV（临时文件），供 STT 使用。

职责边界：
  - 只负责拿到本地文件（必要时下载 URL）、必要时用 ffmpeg 转码、用完清理；
  - 不调用任何模型、不解析语音内容、不做繁简转换；
  - 不污染项目目录：临时文件放系统临时目录，请求结束（含异常）一定删除。

这样替换 STT 引擎（例如换成 Qwen3-ASR）时只需要换 Provider，不用动 Agent。
"""

import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
import wave

# 下载远程音频的大小上限（避免一个链接把手机内存打满）
MAX_AUDIO_BYTES = 20 * 1024 * 1024


class PreparedAudio(object):
    """预处理结果：要么有可用 WAV 路径，要么有明确 error。"""

    def __init__(self, path: str = None, temporary: bool = False, error: str = "",
                 detail: str = ""):
        self.path = path
        self.temporary = temporary
        self.error = error
        self.detail = detail

    @property
    def ok(self) -> bool:
        return bool(self.path) and not self.error

    def cleanup(self) -> None:
        """删除临时文件（只删自己创建的；调用方的原始文件绝不动）。"""
        if self.temporary and self.path:
            try:
                os.remove(self.path)
            except OSError:
                pass
        self.path = None

    def __repr__(self) -> str:
        return "PreparedAudio(path=%r, temporary=%s, error=%r)" % (
            self.path, self.temporary, self.error)


def is_target_wav(path: str) -> bool:
    """是否已经是 16 kHz / 单声道 / 16-bit PCM 的 WAV（满足就直接用，不转码）。"""
    try:
        with wave.open(path, "rb") as handle:
            return (handle.getnchannels() == 1 and handle.getframerate() == 16000
                    and handle.getsampwidth() == 2 and handle.getnframes() > 0)
    except Exception:
        return False


def is_remote(source: str) -> bool:
    return str(source or "").strip().lower().startswith(("http://", "https://"))


def download(source: str, timeout: int = 30, size_limit: int = MAX_AUDIO_BYTES,
             temp_dir: str = None) -> PreparedAudio:
    """把远程音频下载到临时文件（只下载，不解码）。"""
    target = os.path.join(temp_dir or tempfile.gettempdir(),
                          "mobile-agent-audio-%s.src" % uuid.uuid4().hex)
    try:
        with urllib.request.urlopen(str(source).strip(), timeout=timeout) as response:
            data = response.read(size_limit + 1)
    except urllib.error.HTTPError as exc:
        return PreparedAudio(error="audio_download_failed: HTTP %s" % exc.code)
    except Exception as exc:
        return PreparedAudio(error="audio_download_failed: %s" % str(exc)[:120])
    if len(data) > size_limit:
        return PreparedAudio(error="audio_download_failed: 音频超过 %d MB 上限"
                                   % (size_limit // 1024 // 1024))
    try:
        with open(target, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        return PreparedAudio(error="audio_download_failed: 写入失败 %s" % exc)
    return PreparedAudio(path=target, temporary=True)


def ensure_wav(source: str, ffmpeg_bin: str = "ffmpeg", timeout: int = 60,
               temp_dir: str = None) -> PreparedAudio:
    """把 source（本地路径或 http URL）准备成 16k/mono/s16le WAV。

    返回 PreparedAudio：调用方必须在 finally 里执行 cleanup()。
    """
    source = str(source or "").strip()
    if not source:
        return PreparedAudio(error="audio_empty: 音频附件为空")

    downloaded = False
    if is_remote(source):
        fetched = download(source, timeout=timeout, temp_dir=temp_dir)
        if not fetched.ok:
            return fetched
        local = fetched.path
        downloaded = True
    else:
        local = source
        if not os.path.isfile(local):
            return PreparedAudio(error="audio_not_found: 找不到音频文件 %s" % local)

    if is_target_wav(local):
        return PreparedAudio(path=local, temporary=downloaded)

    binary = shutil.which(ffmpeg_bin) or (ffmpeg_bin if os.path.isfile(ffmpeg_bin) else "")
    if not binary:
        if downloaded:
            _remove(local)
        return PreparedAudio(error="audio_preprocess_unavailable: 找不到 ffmpeg（%s）"
                                   % ffmpeg_bin)

    target = os.path.join(temp_dir or tempfile.gettempdir(),
                          "mobile-agent-audio-%s.wav" % uuid.uuid4().hex)
    command = [binary, "-y", "-hide_banner", "-loglevel", "error", "-i", local,
               "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", target]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        _remove(target)
        if downloaded:
            _remove(local)
        return PreparedAudio(error="audio_convert_failed: ffmpeg 超时（%ds）" % timeout)
    except Exception as exc:
        _remove(target)
        if downloaded:
            _remove(local)
        return PreparedAudio(error="audio_convert_failed: %s" % str(exc)[:120])

    if downloaded:
        _remove(local)
    if result.returncode != 0 or not is_target_wav(target):
        detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        _remove(target)
        return PreparedAudio(error="audio_convert_failed: ffmpeg 退出码 %s"
                                   % result.returncode,
                             detail=(detail[-1][:160] if detail else ""))
    return PreparedAudio(path=target, temporary=True)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


__all__ = ["PreparedAudio", "ensure_wav", "download", "is_target_wav", "is_remote",
           "MAX_AUDIO_BYTES"]
