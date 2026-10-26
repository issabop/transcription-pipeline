// Types and calls for the transcription API (see /docs on the server).

export type JobStatus = "queued" | "processing" | "done" | "failed"

export interface Word {
  start: number
  end: number
  text: string
}

export interface Segment {
  id: number
  start: number
  end: number
  text: string
  words: Word[]
}

export interface Pause {
  start: number
  end: number
  duration: number
}

export interface Transcript {
  language: string
  language_probability: number
  duration: number
  text: string
  segments: Segment[]
  pauses: Pause[]
  stats: {
    word_count: number
    speech_seconds: number
    speech_ratio: number
    words_per_minute: number
  }
}

export interface Job {
  id: string
  status: JobStatus
  language: string | null
  progress: number
  attempts: number
  error: string | null
  result: Transcript | null
}

export interface Health {
  model: string
  device: string
  max_upload_mb: number
  max_duration_minutes: number
}

async function errorMessage(response: Response): Promise<string> {
  // A proxy in front of the app (e.g. Cloudflare) can reject a big upload before the app sees it.
  if (response.status === 413) return "File is too large to upload"
  try {
    const body = await response.json()
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)
  } catch {
    return `Request failed (${response.status})`
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.json()
}

export const getHealth = () => request<Health>("/health")

export const getLanguages = () => request<string[]>("/v1/languages")

export function createJob(file: File, language: string | null): Promise<Job> {
  const form = new FormData()
  form.append("file", file)
  if (language) form.append("language", language)
  return request<Job>("/v1/transcriptions", { method: "POST", body: form })
}

export const getJob = (id: string) => request<Job>(`/v1/transcriptions/${id}`)

export function exportUrl(id: string, format: "srt" | "vtt" | "txt", pauses = false): string {
  return `/v1/transcriptions/${id}/transcript?format=${format}${pauses ? "&pauses=true" : ""}`
}
