import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var showCamera = false
    @State private var showAdmin = false

    var body: some View {
        Group {
            if model.authorized {
                main
            } else {
                SetupView()
            }
        }
        .sheet(isPresented: $showAdmin) { AdminView() }
        .alert(model.message ?? "", isPresented: Binding(
            get: { model.message != nil },
            set: { if !$0 { model.message = nil } }
        )) {
            Button("OK", role: .cancel) {}
        }
        // Synchronisation toutes les 30 s tant que l'app est au premier plan.
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            model.refreshLocalState()
            while !Task.isCancelled {
                await model.sync()
                try? await Task.sleep(nanoseconds: 30_000_000_000)
            }
        }
    }

    private var main: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Devoir du jour")
                .font(.title.bold())
                .onLongPressGesture { showAdmin = true }

            VStack(alignment: .leading, spacing: 6) {
                Text(model.statusText)
                if let error = model.lastError {
                    Text("Hors ligne : \(error)").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(model.unlocked ? Color.green.opacity(0.2) : Color.orange.opacity(0.2))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            if let state = model.state, let enonce = state.enonce, state.date == UnlockToken.today() {
                ExerciseWebView(titre: state.titre ?? "", enonce: enonce, dureeMin: state.dureeMin ?? 0)
            } else {
                Spacer()
            }

            if !model.unlocked {
                Text(model.pages.isEmpty ? "Aucune page photographiée" : "\(model.pages.count) page(s) prête(s) à envoyer")
                    .font(.footnote)
                Button {
                    showCamera = true
                } label: {
                    Label("Photographier une page", systemImage: "camera").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.sending)

                HStack {
                    Button {
                        Task { await model.submit() }
                    } label: {
                        Text(model.sending ? "Envoi…" : "Envoyer").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    Button("Effacer", role: .destructive) { model.pages = [] }
                        .buttonStyle(.bordered)
                }
                .disabled(model.pages.isEmpty || model.sending)
            }
        }
        .padding()
        .fullScreenCover(isPresented: $showCamera) {
            CameraPicker { model.pages.append($0) }
                .ignoresSafeArea()
        }
    }
}

struct SetupView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Devoir du jour").font(.title.bold())
            Text(
                model.isChildMode
                    ? "L'application a besoin de l'autorisation Screen Time. Un parent du Partage familial "
                        + "doit l'approuver avec son identifiant Apple."
                    : "L'application a besoin de l'autorisation Screen Time pour bloquer les autres apps "
                        + "tant que le devoir du jour n'est pas validé."
            )
            if let error = model.lastError {
                Text(error).foregroundStyle(.red)
            }
            Button("Autoriser") {
                Task { await model.requestAuthorization() }
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding()
    }
}
