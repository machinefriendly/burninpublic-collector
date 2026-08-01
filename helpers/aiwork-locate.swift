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
        let line = "\(l.coordinate.latitude) \(l.coordinate.longitude) \(l.horizontalAccuracy)"
        print(line)
        // Also drop the fix to a file: when launched as an .app via `open`,
        // stdout is not connected to the caller.
        // This file is an exact GPS fix. It must never exist world-readable,
        // even briefly: create the temp WITH 0600 in the same call, then
        // rename into place. If any step fails, leave no fix behind at all —
        // the caller then errors loudly instead of reading a stale/exposed
        // file.
        let fm = FileManager.default
        let dir = fm.homeDirectoryForCurrentUser.appendingPathComponent(".aiwork")
        let out = dir.appendingPathComponent("last_fix.txt")
        let tmp = dir.appendingPathComponent("last_fix.txt.tmp")
        let ok = fm.createFile(atPath: tmp.path,
                               contents: Data((line + "\n").utf8),
                               attributes: [.posixPermissions: 0o600])
        do {
            if !ok { throw CocoaError(.fileWriteUnknown) }
            try? fm.removeItem(at: out)
            try fm.moveItem(at: tmp, to: out)
        } catch {
            try? fm.removeItem(at: tmp)
            try? fm.removeItem(at: out)
            FileHandle.standardError.write(
                Data("error: could not write fix privately\n".utf8))
            exit(1)
        }
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
