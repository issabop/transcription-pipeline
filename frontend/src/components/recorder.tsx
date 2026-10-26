import { useEffect, useRef, useState } from "react"
import { Mic, RotateCcw, Square } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

// Formats the browser may record in, best first (Chrome/Firefox: WebM/Opus; Safari: MP4/AAC).
const FORMATS = [
  { mime: "audio/webm;codecs=opus", ext: "webm" },
  { mime: "audio/webm", ext: "webm" },
  { mime: "audio/ogg;codecs=opus", ext: "ogg" },
  { mime: "audio/mp4", ext: "m4a" },
]

type State = "idle" | "recording" | "recorded"

function elapsed(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

function micError(error: unknown): string {
  const name = (error as DOMException)?.name
  if (name === "NotAllowedError") return "Microphone access was blocked. Allow it in your browser's site settings and try again."
  if (name === "NotFoundError") return "No microphone was found."
  return "Could not start recording."
}

export function Recorder({
  maxSeconds,
  disabled,
  onRecorded,
  onError,
}: {
  maxSeconds: number
  disabled: boolean
  onRecorded: (file: File | null) => void
  onError: (message: string) => void
}) {
  const [state, setState] = useState<State>("idle")
  const [seconds, setSeconds] = useState(0)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const ticker = useRef<number | undefined>(undefined)

  // Release the microphone and the preview if the component goes away mid-recording.
  useEffect(
    () => () => {
      window.clearInterval(ticker.current)
      recorder.current?.stream.getTracks().forEach((t) => t.stop())
    },
    [],
  )
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])

  async function start() {
    const format = FORMATS.find((f) => MediaRecorder.isTypeSupported(f.mime))
    let stream: MediaStream
    try {
      // The same clean-up a call app uses: no echo, less background noise, steady volume.
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
    } catch (e) {
      onError(micError(e))
      return
    }

    const chunks: Blob[] = []
    const rec = new MediaRecorder(stream, format ? { mimeType: format.mime } : undefined)
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data)
    rec.onstop = () => {
      window.clearInterval(ticker.current)
      stream.getTracks().forEach((t) => t.stop())
      const blob = new Blob(chunks, { type: rec.mimeType })
      const ext = FORMATS.find((f) => rec.mimeType.startsWith(f.mime.split(";")[0]))?.ext ?? "webm"
      setPreviewUrl(URL.createObjectURL(blob))
      setState("recorded")
      onRecorded(new File([blob], `recording.${ext}`, { type: rec.mimeType }))
    }

    recorder.current = rec
    rec.start(1000) // hand over data every second, so nothing is lost if the tab is closed late
    setSeconds(0)
    setState("recording")
    onRecorded(null)
    ticker.current = window.setInterval(() => {
      setSeconds((s) => {
        // Stop at the server's length limit, so the recording is never rejected for being too long
        if (s + 1 >= maxSeconds && rec.state === "recording") rec.stop()
        return s + 1
      })
    }, 1000)
  }

  function stop() {
    if (recorder.current?.state === "recording") recorder.current.stop()
  }

  function reset() {
    setPreviewUrl(null)
    setSeconds(0)
    setState("idle")
    onRecorded(null)
  }

  if (state === "recording") {
    return (
      <div className="flex items-center gap-3">
        <Button type="button" variant="outline" onClick={stop}>
          <Square className="fill-current" /> Stop
        </Button>
        <Badge variant="secondary" className="gap-1.5 tabular-nums">
          <span className="size-2 animate-pulse rounded-full bg-foreground" />
          Recording {elapsed(seconds)}
        </Badge>
      </div>
    )
  }

  if (state === "recorded" && previewUrl) {
    return (
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <audio src={previewUrl} controls className="h-9 w-full sm:max-w-sm" />
        {/* Browsers can't read a recording's length from its header, so show the one we timed. */}
        <span className="text-sm text-muted-foreground tabular-nums">{elapsed(seconds)} recorded</span>
        <Button type="button" variant="ghost" size="sm" onClick={reset} disabled={disabled}>
          <RotateCcw /> Record again
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button type="button" variant="outline" onClick={start} disabled={disabled}>
        <Mic /> Start recording
      </Button>
      <span className="text-sm text-muted-foreground">Your browser will ask for microphone access.</span>
    </div>
  )
}
