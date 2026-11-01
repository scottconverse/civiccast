import Hls from 'hls.js'
import { describe, expect, it, vi } from 'vitest'

interface LiveController {
  media: HTMLVideoElement | null
  getLoadPosition: () => number
  synchronizeToLiveEdge: (details: {
    fragmentStart: number
    edge: number
    targetduration: number
  }) => void
}

function synchronize(bufferedPosition: number, maximumSegments?: number) {
  const hls = new Hls({
    enableWorker: false,
    liveSyncDurationCount: 3,
    ...(maximumSegments === undefined ? {} : { liveMaxLatencyDurationCount: maximumSegments }),
  })
  const controller = Reflect.get(hls, 'streamController') as LiveController
  const media = document.createElement('video')
  media.currentTime = bufferedPosition
  Object.defineProperty(media, 'duration', { value: 468 })
  Object.defineProperty(media, 'readyState', { value: 4 })
  controller.media = media
  vi.spyOn(controller, 'getLoadPosition').mockImplementation(() => media.currentTime)
  vi.spyOn(hls, 'liveSyncPosition', 'get').mockReturnValue(462)
  try {
    // R9 Public's first-picture screenshot: 4:46 / 7:48, readyState 4.
    // Exercise the bundled controller itself, not a simulated seek policy.
    controller.synchronizeToLiveEdge({ fragmentStart: 456, edge: 468, targetduration: 2 })
    return media.currentTime
  } finally {
    controller.media = null
    hls.destroy()
  }
}

describe('live buffer recovery using the bundled hls.js controller', () => {
  it('reproduces the default retaining a ready player three minutes behind', () => {
    expect(synchronize(286)).toBe(286)
  })

  it('recovers that stale player to the ordinary live buffer', () => {
    expect(synchronize(286, 6)).toBe(462)
  })

  it('preserves healthy playback within the maximum live buffer', () => {
    expect(synchronize(460, 6)).toBe(460)
  })
})
