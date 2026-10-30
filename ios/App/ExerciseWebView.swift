import SwiftUI
import WebKit

/// Affiche l'énoncé (Markdown + LaTeX) avec marked et KaTeX embarqués dans l'app.
struct ExerciseWebView: UIViewRepresentable {
    let titre: String
    let enonce: String
    let dureeMin: Int

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.navigationDelegate = context.coordinator
        webView.isOpaque = false
        if let url = Bundle.main.url(forResource: "devoir", withExtension: "html", subdirectory: "web") {
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.render(webView, data: ["titre": titre, "enonce": enonce, "duree_min": dureeMin])
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        private var loaded = false
        private var pending: [String: Any]?
        private var rendered: String?

        func render(_ webView: WKWebView, data: [String: Any]) {
            pending = data
            guard loaded else { return }
            guard let json = try? JSONSerialization.data(withJSONObject: data),
                  let text = String(data: json, encoding: .utf8), text != rendered
            else { return }
            rendered = text
            webView.evaluateJavaScript("render(\(text))")
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            loaded = true
            if let pending { render(webView, data: pending) }
        }

        // Aucune navigation possible depuis l'énoncé.
        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            decisionHandler(navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
        }
    }
}
