import Foundation
import Vision
import ImageIO
import CoreGraphics

struct OCRBox: Codable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OCRLine: Codable {
    let text: String
    let confidence: Double
    let bbox: OCRBox
}

struct OCRResult: Codable {
    let schemaVersion: Int
    let engine: String
    let input: String
    let languages: [String]
    let averageConfidence: Double
    let text: String
    let lines: [OCRLine]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case engine
        case input
        case languages
        case averageConfidence = "average_confidence"
        case text
        case lines
    }
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

guard CommandLine.arguments.count == 2 else {
    fail("Usage: swift vision_ocr.swift <image-path>", code: 2)
}

let inputPath = CommandLine.arguments[1]
let inputURL = URL(fileURLWithPath: inputPath)
guard let source = CGImageSourceCreateWithURL(inputURL as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fail("Cannot decode image: \(inputPath)")
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.minimumTextHeight = 0.006

let preferredLanguages = ["zh-Hans", "zh-Hant", "en-US"]
do {
    let supported = try VNRecognizeTextRequest.supportedRecognitionLanguages(
        for: .accurate,
        revision: request.revision
    )
    let selected = preferredLanguages.filter { supported.contains($0) }
    if !selected.isEmpty {
        request.recognitionLanguages = selected
    }
} catch {
    // Vision can still auto-select a supported language when discovery fails.
}

do {
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
} catch {
    fail("Vision OCR failed: \(error.localizedDescription)")
}

let observations = request.results ?? []
let ordered = observations.sorted { left, right in
    let yGap = abs(left.boundingBox.maxY - right.boundingBox.maxY)
    if yGap > 0.012 {
        return left.boundingBox.maxY > right.boundingBox.maxY
    }
    return left.boundingBox.minX < right.boundingBox.minX
}

let lines: [OCRLine] = ordered.compactMap { observation in
    guard let candidate = observation.topCandidates(1).first else { return nil }
    let box = observation.boundingBox
    return OCRLine(
        text: candidate.string,
        confidence: Double(candidate.confidence),
        bbox: OCRBox(
            x: Double(box.origin.x),
            y: Double(box.origin.y),
            width: Double(box.size.width),
            height: Double(box.size.height)
        )
    )
}

let average = lines.isEmpty
    ? 0.0
    : lines.reduce(0.0) { $0 + $1.confidence } / Double(lines.count)
let result = OCRResult(
    schemaVersion: 1,
    engine: "apple-vision",
    input: inputPath,
    languages: request.recognitionLanguages,
    averageConfidence: average,
    text: lines.map(\.text).joined(separator: "\n"),
    lines: lines
)

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
do {
    let data = try encoder.encode(result)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fail("Cannot encode OCR result: \(error.localizedDescription)")
}
