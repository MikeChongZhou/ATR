// stdout protocol: ATR1, then little-endian mono Float32 PCM at 48000 Hz.
// All diagnostics go to stderr. No screen frames are exported or saved.
import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

@available(macOS 13.0, *)
final class AudioOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    private let writeLock = NSLock()
    private var ready = false

    func beginOutput() {
        writeLock.lock()
        defer { writeLock.unlock() }
        FileHandle.standardOutput.write(Data("ATR1".utf8))
        ready = true
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail("ScreenCaptureKit stopped: \(error.localizedDescription)")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, sample.isValid,
              let description = sample.formatDescription else { return }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        guard format.commonFormat == .pcmFormatFloat32,
              format.sampleRate == 48000, format.channelCount == 1 else {
            fail("ScreenCaptureKit returned an unsupported audio format.")
        }
        var block: CMBlockBuffer?
        var list = AudioBufferList(mNumberBuffers: 1,
                                  mBuffers: AudioBuffer(mNumberChannels: 1, mDataByteSize: 0, mData: nil))
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sample, bufferListSizeNeededOut: nil, bufferListOut: &list,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &block)
        guard status == noErr, let pointer = list.mBuffers.mData else { return }
        writeLock.lock()
        defer { writeLock.unlock() }
        guard ready else { return }
        FileHandle.standardOutput.write(Data(bytes: pointer, count: Int(list.mBuffers.mDataByteSize)))
    }
}

@available(macOS 13.0, *)
func capture() async {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else { fail("No display is available for system audio capture.") }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.capturesAudio = true
        config.sampleRate = 48000
        config.channelCount = 1
        config.excludesCurrentProcessAudio = true
        let output = AudioOutput()
        let stream = SCStream(filter: filter, configuration: config, delegate: output)
        try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: .main)
        try await stream.startCapture()
        output.beginOutput()
        // Retain the stream and delegate for the lifetime of the process.
        while true { try await Task.sleep(nanoseconds: 1_000_000_000) }
    } catch {
        fail("Cannot capture system audio: \(error.localizedDescription). Allow Screen & System Audio Recording in System Settings, then restart ATR.")
    }
}

if #available(macOS 13.0, *) {
    Task { await capture() }
    dispatchMain()
} else {
    fail("System audio capture requires macOS 13 or later.")
}
