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

class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status)
  return response.json()
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
})

export const getHealth = () => request<Health>("/health")

export const getLanguages = () => request<string[]>("/v1/languages")

// Files bigger than this are sent in chunks, so no single request is slow enough to hit a proxy timeout.
const CHUNKED_ABOVE = 2 * 1024 * 1024
const CHUNK_ATTEMPTS = 3

export async function createJob(
  file: File,
  language: string | null,
  onProgress: (fraction: number) => void = () => {},
): Promise<Job> {
  if (file.size <= CHUNKED_ABOVE) {
    const form = new FormData()
    form.append("file", file)
    if (language) form.append("language", language)
    const job = await request<Job>("/v1/transcriptions", { method: "POST", body: form })
    onProgress(1)
    return job
  }

  const session = await request<{ id: string; chunk_size: number }>("/v1/uploads", json({ size: file.size }))
  for (let offset = 0; offset < file.size; offset += session.chunk_size) {
    const chunk = file.slice(offset, offset + session.chunk_size)
    await withRetries(() =>
      request(`/v1/uploads/${session.id}?offset=${offset}`, {
        method: "PUT",
        headers: { "Content-Type": "application/octet-stream" },
        body: chunk,
      }),
    )
    onProgress((offset + chunk.size) / file.size)
  }
  return request<Job>(`/v1/uploads/${session.id}/complete`, json({ language }))
}

// Network blips and server errors are retried; a 4xx means the request itself is wrong, so it isn't.
async function withRetries<T>(send: () => Promise<T>): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await send()
    } catch (e) {
      const clientError = e instanceof ApiError && e.status < 500
      if (clientError || attempt >= CHUNK_ATTEMPTS) throw e
      await new Promise((r) => setTimeout(r, 1000 * attempt))
    }
  }
}

export const getJob = (id: string) => request<Job>(`/v1/transcriptions/${id}`)

export function exportUrl(id: string, format: "srt" | "vtt" | "txt", pauses = false): string {
  return `/v1/transcriptions/${id}/transcript?format=${format}${pauses ? "&pauses=true" : ""}`
}
