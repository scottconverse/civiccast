// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RecordingPlayer } from './RecordingPlayer'

vi.mock('./HlsPlayer', () => ({ HlsPlayer: () => <video aria-label="Original HLS recording" /> }))
const description = {
  original_url: '/original.mp4', described_url: '/described.mp4', captions_url: '/captions.vtt', descriptions_url: '/descriptions.vtt',
  pause_descriptions: [{ id: 0, time: 2, until: 8, text: 'The map shows a park.', audio: '/narration.wav' }],
  coverage: { in_gap: 1, requires_pause: 1, uncertain_visuals: 0 }, limitations: ['Automatic output.'], credits: ['Kyutai'],
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('Recording audio descriptions', () => {
  it.each([false, true])('queues every crossed cue (video ended=%s)', async (ended) => {
    const payload = { ...description, pause_descriptions: [
      { id: 1, time: 2, until: 2.04, text: 'First short cue.', audio: '/one.wav' },
      { id: 2, time: 2.05, until: 2.09, text: 'Second short cue.', audio: '/two.wav' },
    ] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))))
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const { container } = render(<RecordingPlayer assetId="meeting-123" manifestUrl="/playlist.m3u8" />)
    fireEvent.click(await screen.findByLabelText('Use audio-described playback'))
    const video = screen.getByLabelText('Meeting video player') as HTMLVideoElement
    Object.defineProperty(video, 'duration', { value: 2.4, configurable: true })
    Object.defineProperty(video, 'paused', { value: ended, configurable: true })
    Object.defineProperty(video, 'ended', { value: ended, configurable: true })
    fireEvent.loadedMetadata(video)
    fireEvent.click(screen.getByLabelText('Pause the video to hear descriptions that do not fit between dialogue'))
    video.currentTime = 2.4
    if (ended) fireEvent.ended(video)
    else fireEvent.timeUpdate(video)
    await screen.findByText('Description: First short cue.')
    const audio = container.querySelector('audio')!
    expect(audio.getAttribute('src')).toBe('/one.wav')
    fireEvent.ended(audio)
    await screen.findByText('Description: Second short cue.')
    expect(audio.getAttribute('src')).toBe('/two.wav')
    fireEvent.ended(audio)
    fireEvent.timeUpdate(video)
    expect(screen.queryByRole('button', { name: 'Skip description and resume video' })).toBeNull()
  })

  it('does not replay cues jumped over by a seek, but permits replay after seeking back', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(description))))
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    render(<RecordingPlayer assetId="meeting-123" manifestUrl="/playlist.m3u8" />)
    fireEvent.click(await screen.findByLabelText('Use audio-described playback'))
    const video = screen.getByLabelText('Meeting video player') as HTMLVideoElement
    Object.defineProperty(video, 'duration', { value: 30, configurable: true })
    Object.defineProperty(video, 'paused', { value: false, configurable: true })
    fireEvent.loadedMetadata(video)
    fireEvent.click(screen.getByLabelText('Pause the video to hear descriptions that do not fit between dialogue'))
    video.currentTime = 12
    fireEvent.seeking(video)
    fireEvent.timeUpdate(video)
    expect(screen.queryByRole('button', { name: 'Skip description and resume video' })).toBeNull()
    video.currentTime = 1
    fireEvent.seeking(video)
    video.currentTime = 2.3
    fireEvent.timeUpdate(video)
    await screen.findByText('Description: The map shows a park.')
  })

  it('preserves existing HLS when there is no described derivative', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 404 })))
    render(<RecordingPlayer assetId="meeting-123" manifestUrl="/playlist.m3u8" />)
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
    expect(screen.getByLabelText('Original HLS recording')).toBeTruthy()
    expect(screen.queryByLabelText('Use audio-described playback')).toBeNull()
  })

  it('switches explicitly, preserves the clock, and pauses before narration', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(description))))
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    render(<RecordingPlayer assetId="meeting-123" manifestUrl="/playlist.m3u8" />)
    const choice = await screen.findByLabelText('Use audio-described playback')
    const original = screen.getByLabelText('Original HLS recording') as HTMLVideoElement
    original.currentTime = 3
    fireEvent.click(choice)
    const video = screen.getByLabelText('Meeting video player') as HTMLVideoElement
    Object.defineProperty(video, 'duration', { value: 30, configurable: true })
    Object.defineProperty(video, 'paused', { value: false, configurable: true })
    fireEvent.loadedMetadata(video)
    expect(video.currentTime).toBe(3)
    expect(video.getAttribute('src')).toBe('/described.mp4')
    fireEvent.click(screen.getByLabelText('Pause the video to hear descriptions that do not fit between dialogue'))
    fireEvent.timeUpdate(video)
    await screen.findByText('Description: The map shows a park.')
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Skip description and resume video' }))
    fireEvent.timeUpdate(video)
    expect(screen.queryByRole('button', { name: 'Skip description and resume video' })).toBeNull()
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
  })
})
