#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <ImageIO/ImageIO.h>
#import <CoreGraphics/CoreGraphics.h>

static void Fail(NSString *message, int code) {
    fprintf(stderr, "%s\n", message.UTF8String);
    exit(code);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            Fail(@"Usage: vision_ocr <image-path>", 2);
        }

        NSString *inputPath = [NSString stringWithUTF8String:argv[1]];
        NSURL *inputURL = [NSURL fileURLWithPath:inputPath];
        CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)inputURL, NULL);
        if (source == NULL) {
            Fail([NSString stringWithFormat:@"Cannot open image: %@", inputPath], 1);
        }
        CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
        CFRelease(source);
        if (image == NULL) {
            Fail([NSString stringWithFormat:@"Cannot decode image: %@", inputPath], 1);
        }

        VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        request.usesLanguageCorrection = YES;
        request.minimumTextHeight = 0.006;
        request.recognitionLanguages = @[@"zh-Hans", @"zh-Hant", @"en-US"];

        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
        NSError *visionError = nil;
        BOOL succeeded = [handler performRequests:@[request] error:&visionError];
        CGImageRelease(image);
        if (!succeeded) {
            Fail([NSString stringWithFormat:@"Vision OCR failed: %@", visionError.localizedDescription], 1);
        }

        NSArray<VNRecognizedTextObservation *> *observations = request.results ?: @[];
        observations = [observations sortedArrayUsingComparator:^NSComparisonResult(
            VNRecognizedTextObservation *left,
            VNRecognizedTextObservation *right
        ) {
            CGFloat yGap = fabs(CGRectGetMaxY(left.boundingBox) - CGRectGetMaxY(right.boundingBox));
            if (yGap > 0.012) {
                return CGRectGetMaxY(left.boundingBox) > CGRectGetMaxY(right.boundingBox)
                    ? NSOrderedAscending : NSOrderedDescending;
            }
            return CGRectGetMinX(left.boundingBox) < CGRectGetMinX(right.boundingBox)
                ? NSOrderedAscending : NSOrderedDescending;
        }];

        NSMutableArray *lines = [NSMutableArray array];
        NSMutableArray<NSString *> *textLines = [NSMutableArray array];
        double confidenceSum = 0.0;
        for (VNRecognizedTextObservation *observation in observations) {
            VNRecognizedText *candidate = [observation topCandidates:1].firstObject;
            if (candidate == nil) {
                continue;
            }
            CGRect box = observation.boundingBox;
            [lines addObject:@{
                @"text": candidate.string,
                @"confidence": @(candidate.confidence),
                @"bbox": @{
                    @"x": @(box.origin.x),
                    @"y": @(box.origin.y),
                    @"width": @(box.size.width),
                    @"height": @(box.size.height)
                }
            }];
            [textLines addObject:candidate.string];
            confidenceSum += candidate.confidence;
        }

        NSDictionary *result = @{
            @"schema_version": @1,
            @"engine": @"apple-vision-objc",
            @"input": inputPath,
            @"languages": request.recognitionLanguages ?: @[],
            @"average_confidence": @(lines.count ? confidenceSum / lines.count : 0.0),
            @"text": [textLines componentsJoinedByString:@"\n"],
            @"lines": lines
        };
        NSError *jsonError = nil;
        NSData *data = [NSJSONSerialization dataWithJSONObject:result
                                                       options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys
                                                         error:&jsonError];
        if (data == nil) {
            Fail([NSString stringWithFormat:@"Cannot encode OCR JSON: %@", jsonError.localizedDescription], 1);
        }
        fwrite(data.bytes, 1, data.length, stdout);
        fputc('\n', stdout);
    }
    return 0;
}
