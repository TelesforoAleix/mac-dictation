import Foundation
import NaturalLanguage

struct Input: Decodable { let texts: [String] }
struct Analysis: Encodable { let language: String?; let confidence: Double?; let names: [String] }
let input = try JSONDecoder().decode(Input.self, from: FileHandle.standardInput.readDataToEndOfFile())
let analyses: [Analysis] = input.texts.map { text in
    let recognizer = NLLanguageRecognizer()
    recognizer.processString(text)
    let language = recognizer.dominantLanguage
    let confidence = language.flatMap { recognizer.languageHypotheses(withMaximum: 1)[$0] }
    let tagger = NLTagger(tagSchemes: [.nameType])
    tagger.string = text
    var names = [String]()
    tagger.enumerateTags(in: text.startIndex..<text.endIndex, unit: .word,
                         scheme: .nameType, options: [.omitWhitespace, .omitPunctuation, .joinNames]) { tag, range in
        if tag == .personalName || tag == .placeName || tag == .organizationName {
            names.append(String(text[range]))
        }
        return true
    }
    return Analysis(language: language?.rawValue, confidence: confidence, names: names)
}
let output = try JSONEncoder().encode(analyses)
FileHandle.standardOutput.write(output)
