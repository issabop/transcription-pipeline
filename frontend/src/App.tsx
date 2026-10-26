import { useEffect, useRef, useState } from "react"
import { AlertCircle, Code, Download, FileAudio, Loader2 } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { createJob, exportUrl, getHealth, getJob, type Health, type Job, type Segment, type Transcript } from "@/api"

const REPO_URL = "https://github.com/issabop/transcription-pipeline"
const POLL_MS = 1000

const LANGUAGES = [
  ["auto", "Detect automatically"],
  ["en", "English"],
  ["ur", "Urdu"],
  ["hi", "Hindi"],
  ["ar", "Arabic"],
  ["fr", "French"],
  ["de", "German"],
  ["es", "Spanish"],
] as const

function clock(seconds: number): string {
  const whole = Math.floor(seconds)
  const tenths = Math.round((seconds - whole) * 10) % 10
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const s = whole % 60
  const mmss = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${tenths}`
  return h ? `${h}:${mmss}` : mmss
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [language, setLanguage] = useState("auto")
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [showPauses, setShowPauses] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null))
    return () => window.clearTimeout(timer.current)
  }, [])

  const busy = uploading || job?.status === "queued" || job?.status === "processing"

  function poll(id: string) {
    timer.current = window.setTimeout(async () => {
      try {
        const next = await getJob(id)
        setJob(next)
        if (next.status === "queued" || next.status === "processing") poll(id)
      } catch (e) {
        setError((e as Error).message)
      }
    }, POLL_MS)
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!file) return
    window.clearTimeout(timer.current)
    setError(null)
    setJob(null)
    setUploading(true)
    try {
      const created = await createJob(file, language === "auto" ? null : language)
      setJob(created)
      poll(created.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="min-h-svh bg-background text-foreground">
      <main className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-10 sm:py-14">
        <header className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">Transcription Pipeline</h1>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" asChild>
                <a href="/docs">API docs</a>
              </Button>
              <Button variant="ghost" size="sm" asChild>
                <a href={REPO_URL} target="_blank" rel="noreferrer">
                  <Code /> Source
                </a>
              </Button>
            </div>
          </div>
          <p className="text-muted-foreground">
            Upload an audio or video file and get a timestamped transcript, with word timings, pauses and stats.
          </p>
        </header>

        <Card>
          <CardHeader>
            <CardTitle>New transcription</CardTitle>
            <CardDescription>
              MP3, WAV, M4A, FLAC, OGG or a video file
              {health && ` · up to ${health.max_upload_mb} MB and ${health.max_duration_minutes} minutes`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="flex flex-col gap-5">
              <div className="grid gap-5 sm:grid-cols-[1fr_220px]">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="file">File</Label>
                  <Input
                    id="file"
                    type="file"
                    accept="audio/*,video/*"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    disabled={busy}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="language">Language</Label>
                  <Select value={language} onValueChange={setLanguage} disabled={busy}>
                    <SelectTrigger id="language" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {LANGUAGES.map(([code, name]) => (
                        <SelectItem key={code} value={code}>
                          {name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div>
                <Button type="submit" disabled={!file || busy}>
                  {busy ? <Loader2 className="animate-spin" /> : <FileAudio />}
                  {uploading ? "Uploading…" : busy ? "Transcribing…" : "Transcribe"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        {error && (
          <Alert>
            <AlertCircle />
            <AlertTitle>Something went wrong</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {job && job.status !== "done" && <JobStatusCard job={job} />}

        {job?.status === "done" && job.result && (
          <ResultCard id={job.id} transcript={job.result} showPauses={showPauses} onShowPauses={setShowPauses} />
        )}

        {health && (
          <p className="text-center text-xs text-muted-foreground">
            Running {health.model} on {health.device.toUpperCase()}
          </p>
        )}
      </main>
    </div>
  )
}

function JobStatusCard({ job }: { job: Job }) {
  const failed = job.status === "failed"
  const label = failed
    ? "Failed"
    : job.status === "queued"
      ? job.error ?? "Waiting in the queue…"
      : "Transcribing…"
  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4 text-sm">
          <div className="flex items-center gap-2">
            <Badge variant={failed ? "outline" : "secondary"}>{job.status}</Badge>
            <span className="text-muted-foreground">{failed ? job.error : label}</span>
          </div>
          {!failed && <span className="tabular-nums text-muted-foreground">{Math.round(job.progress * 100)}%</span>}
        </div>
        {!failed && <Progress value={job.progress * 100} />}
      </CardContent>
    </Card>
  )
}

function ResultCard({
  id,
  transcript,
  showPauses,
  onShowPauses,
}: {
  id: string
  transcript: Transcript
  showPauses: boolean
  onShowPauses: (value: boolean) => void
}) {
  const t = transcript
  const stats = [
    ["Language", `${t.language} · ${Math.round(t.language_probability * 100)}%`],
    ["Duration", clock(t.duration)],
    ["Words", String(t.stats.word_count)],
    ["Words / min", String(t.stats.words_per_minute)],
    ["Speech", `${Math.round(t.stats.speech_ratio * 100)}%`],
    ["Pauses", String(t.pauses.length)],
  ]
  // Pauses are keyed by the end time of the word just before them.
  const pauseAfter = new Map(t.pauses.map((p) => [p.start, p]))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Transcript</CardTitle>
        <CardDescription>{t.segments.length} segments</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          {stats.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-medium tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>

        <Separator />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Switch id="pauses" checked={showPauses} onCheckedChange={onShowPauses} />
            <Label htmlFor="pauses">Show pauses</Label>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" asChild>
              <a href={exportUrl(id, "srt")}>
                <Download /> SRT
              </a>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <a href={exportUrl(id, "vtt")}>
                <Download /> VTT
              </a>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <a href={exportUrl(id, "txt", showPauses)}>
                <Download /> TXT
              </a>
            </Button>
          </div>
        </div>

        <ScrollArea className="h-[420px] rounded-lg border">
          <ol className="divide-y">
            {t.segments.map((s) => (
              <SegmentRow key={s.id} segment={s} showPauses={showPauses} pauseAfter={pauseAfter} />
            ))}
          </ol>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}

function SegmentRow({
  segment,
  showPauses,
  pauseAfter,
}: {
  segment: Segment
  showPauses: boolean
  pauseAfter: Map<number, { duration: number }>
}) {
  const words = segment.words
  const trailing = showPauses && words.length ? pauseAfter.get(words[words.length - 1].end) : undefined

  return (
    <li className="px-4 py-3">
      <div className="grid gap-1 sm:grid-cols-[120px_1fr] sm:gap-4">
        <time className="pt-0.5 font-mono text-xs text-muted-foreground tabular-nums">
          {clock(segment.start)} → {clock(segment.end)}
        </time>
        <p dir="auto" className="leading-relaxed">
          {showPauses && words.length
            ? words.map((w, i) => {
                const pause = i < words.length - 1 ? pauseAfter.get(w.end) : undefined
                return (
                  <span key={i}>
                    {i > 0 && " "}
                    {w.text}
                    {pause && <PauseBadge duration={pause.duration} />}
                  </span>
                )
              })
            : segment.text}
        </p>
      </div>
      {trailing && (
        <div className="mt-2 sm:pl-[136px]">
          <PauseBadge duration={trailing.duration} />
        </div>
      )}
    </li>
  )
}

function PauseBadge({ duration }: { duration: number }) {
  return (
    <Badge variant="outline" className="mx-1 align-middle font-mono text-[11px]">
      pause {duration.toFixed(1)}s
    </Badge>
  )
}
