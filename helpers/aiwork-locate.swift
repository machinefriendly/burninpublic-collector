// Tiny CoreLocation helper. Prints "lat lon accuracy_m" and exits.
// Compiled with an embedded Info.plist (see helpers/Info.plist) so macOS TCC
// can attribute the permission prompt — plain python/scripts get silently
// denied on modern macOS.
import CoreLocation
import Foundation

final class Locator: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func start() {
        manager.requestWhenInUseAuthorization()
        manager.startUpdatingLocation()
    }

    func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        guard let l = locs.last, l.horizontalAccuracy >= 0 else { return }
        print("\(l.coordinate.latitude) \(l.coordinate.longitude) \(l.horizontalAccuracy)")
        exit(0)
    }

    func locationManager(_ m: CLLocationManager, didFailWithError e: Error) {
        FileHandle.standardError.write(Data("error: \(e.localizedDescription)\n".utf8))
    }

    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        let s = m.authorizationStatus
        if s == .denied || s == .restricted {
            FileHandle.standardError.write(Data(
                "denied: enable in System Settings > Privacy & Security > Location Services\n".utf8))
            exit(2)
        }
    }
}

let locator = Locator()
locator.start()
RunLoop.main.run(until: Date(timeIntervalSinceNow: 30))
FileHandle.standardError.write(Data("timeout: no location fix\n".utf8))
exit(1)
