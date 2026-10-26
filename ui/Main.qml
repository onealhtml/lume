import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Effects

Window {
    id: win
    title: "Lume"
    width: 340
    height: 470
    visible: false
    color: "transparent"
    // Tool: sem botao na barra de tarefas / Dock; o Python cuida de posicao e foco
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint

    // ---------- tokens ----------
    readonly property color ink: "#f5f5f7"
    readonly property color ink2: Qt.rgba(245 / 255, 245 / 255, 247 / 255, 0.62)
    readonly property color ink3: Qt.rgba(245 / 255, 245 / 255, 247 / 255, 0.38)
    readonly property color hair: Qt.rgba(1, 1, 1, 0.14)
    readonly property color dark: "#1d1d22"
    readonly property string textFont: backend.textFont
    readonly property string displayFont: backend.displayFont
    readonly property string roundedFont: backend.roundedFont

    // ---------- estado ----------
    readonly property var st: backend.state
    readonly property bool connected: !!st.connected
    readonly property bool lampOn: connected && !!st.on
    readonly property bool colourMode: st.mode === "colour"
    readonly property int bright: st.bright === undefined ? 100 : st.bright
    readonly property int temp: st.temp === undefined ? 50 : st.temp
    readonly property int hue: st.h === undefined ? 30 : st.h
    readonly property int sat: st.s === undefined ? 100 : st.s
    readonly property color lampColor: lampOn ? (colourMode ? Qt.hsva(hue / 360, sat / 100, 1, 1) : whiteColor(temp))
                                              : Qt.rgba(120 / 255, 124 / 255, 140 / 255, 1)
    property string view: "main"
    property bool sheetOpen: false
    property var cfg: ({})

    function whiteColor(t) {
        const k = t / 100
        return Qt.rgba((255 + (205 - 255) * k) / 255, (170 + (222 - 170) * k) / 255, (80 + (255 - 80) * k) / 255, 1)
    }
    function kelvin(t) { return Math.round((2700 + t * 38) / 100) * 100 }
    function hueName(h, s) {
        if (s < 20) return "branco"
        const names = [[15, "vermelho"], [45, "laranja"], [70, "amarelo"], [165, "verde"], [200, "ciano"],
                       [255, "azul"], [290, "roxo"], [340, "rosa"], [361, "vermelho"]]
        return names.find(n => h < n[0])[1]
    }
    function subtitle() {
        if (!connected) return st.status || "Desconectada"
        if (!st.on) return "Apagada"
        if (colourMode) return "Acesa em " + hueName(hue, sat)
        return "Acesa em branco " + (temp < 34 ? "quente" : temp > 66 ? "frio" : "neutro")
    }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, Math.round(v))) }

    // comandos: o Python devolve false quando a lampada nao esta conectada
    function needConnection() {
        if (connected) return false
        toast.show(cfg.key ? "A lâmpada não está conectada" : "Configure a lâmpada primeiro")
        return true
    }
    function send(ok) { if (ok === false) toast.show("A lâmpada não está conectada") }
    function setBright(v) { if (!needConnection()) send(backend.brightness(clamp(v, 1, 100))) }
    function setTemp(v) { if (!needConnection()) send(backend.temperature(clamp(v, 0, 100))) }
    function setColour(h, s) { if (!needConnection()) send(backend.colour(clamp(h, 0, 359), clamp(s, 0, 100))) }

    function showView(name) {
        view = name
        sheetOpen = false
        if (name === "settings" && !settings.dirty) settings.fill()
    }

    Connections {
        target: backend
        function onShowRequested(v) {
            cfg = backend.getConfig()
            showView(v)
            enter.restart()
        }
    }

    onClosing: close => { close.accepted = false; backend.windowClosed() }

    Shortcut {
        sequence: "Esc"
        onActivated: sheetOpen ? sheetOpen = false : backend.hide()
    }

    // ---------- componentes ----------
    // recorta o conteudo num retangulo arredondado (Rectangle.clip so recorta em retangulo reto)
    component RoundClip: Item {
        id: rc
        property real radius: 22
        default property alias content: inner.data
        Item {
            id: inner
            anchors.fill: parent
            layer.enabled: true
            layer.effect: MultiEffect {
                maskEnabled: true
                maskSource: rcMask
                maskThresholdMin: 0.5
                maskSpreadAtMin: 1.0
            }
        }
        Rectangle {
            id: rcMask
            anchors.fill: parent
            radius: rc.radius
            visible: false
            layer.enabled: true
            antialiasing: true
        }
    }

    // vidro: gradiente translucido com o reflexo do topo embutido e borda fina.
    // Nada de formas extras por cima: elas marcavam as laterais dos botoes pequenos.
    component Glass: Rectangle {
        radius: 22
        border.color: hair
        border.width: 1
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.19) }
            GradientStop { position: 0.35; color: Qt.rgba(1, 1, 1, 0.075) }
            GradientStop { position: 0.6; color: Qt.rgba(1, 1, 1, 0.045) }
            GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, 0.08) }
        }
    }

    // icone de traco (24x24) desenhado em SVG na cor pedida
    readonly property var icons: ({
        gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
        sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
        power: '<path d="M12 3v8"/><path d="M6.3 7.2a8 8 0 1 0 11.4 0"/>',
        back: '<path d="M15 18l-6-6 6-6"/>'
    })
    component Icon: Image {
        property string name
        property color tint: ink
        property real stroke: 2
        width: 18; height: 18
        sourceSize: Qt.size(width * 3, height * 3)
        smooth: true
        source: "data:image/svg+xml;utf8," + encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="' + tint.toString().slice(0, 7)
            + '" stroke-opacity="' + tint.a + '" stroke-width="' + stroke + '" stroke-linecap="round" stroke-linejoin="round">'
            + icons[name] + '</svg>')
    }

    component Txt: Text {
        color: ink
        font.family: textFont
        font.pixelSize: 13
        font.hintingPreference: Font.PreferNoHinting
        renderType: Text.QtRendering
    }

    component Btn: Rectangle {
        id: b
        property alias text: t.text
        property bool primary: false
        property bool enabledBtn: true
        signal clicked
        implicitHeight: 36
        implicitWidth: t.implicitWidth + 28
        radius: 12
        color: primary ? (ma.containsMouse ? "#f0f0f3" : "#ffffff") : (ma.containsMouse ? Qt.rgba(1, 1, 1, 0.16) : Qt.rgba(1, 1, 1, 0.09))
        border.color: primary ? "transparent" : hair
        opacity: enabledBtn ? 1 : 0.5
        Txt {
            id: t
            anchors.centerIn: parent
            color: b.primary ? dark : ink
            font.weight: b.primary ? Font.DemiBold : Font.Medium
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: if (b.enabledBtn) b.clicked()
        }
        scale: ma.pressed ? 0.97 : 1
        Behavior on scale { NumberAnimation { duration: 90 } }
    }

    component RoundBtn: Glass {
        id: rb
        property string glyph
        signal clicked
        width: 34; height: 34; radius: 17
        Icon { anchors.centerIn: parent; name: rb.glyph; width: 17; height: 17; stroke: rb.glyph === "gear" ? 1.8 : 2.2 }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: rb.clicked() }
    }

    // ---------- painel ----------
    // so a luz vai para textura (ela ja e desfocada); o texto fica fora de camadas e sai nitido
    Item {
        id: panel
        anchors.fill: parent
        readonly property real radius: backend.systemGlass || backend.windowed ? 0 : 20  // no Windows 11 o proprio sistema arredonda

        // fundo: veu escuro (translucido sobre o acrilico do Windows, quase opaco nos outros)
        Rectangle {
            anchors.fill: parent
            radius: panel.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(38 / 255, 40 / 255, 48 / 255, backend.systemGlass ? 0.22 : backend.windowed ? 1 : 0.96) }
                GradientStop { position: 1.0; color: Qt.rgba(20 / 255, 21 / 255, 26 / 255, backend.systemGlass ? 0.40 : backend.windowed ? 1 : 0.98) }
            }
        }

        // a luz da lampada atras do vidro
        RoundClip {
          anchors.fill: parent
          radius: panel.radius
          Item {
            anchors.fill: parent
            layer.enabled: true
            layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 64; autoPaddingEnabled: false }
            Rectangle {
                width: 240; height: 240; radius: 120
                x: parent.width - 170; y: -40
                color: lampColor
                opacity: lampOn ? 0.25 + bright / 100 * 0.5 : 0.12
                Behavior on color { ColorAnimation { duration: 500 } }
                Behavior on opacity { NumberAnimation { duration: 500 } }
            }
          }
        }

        Item {
            id: content
            anchors.fill: parent
            clip: true
            transform: Translate { id: enterShift }

            // ===== tela principal =====
            Item {
                id: mainView
                width: parent.width; height: parent.height
                x: view === "settings" ? -width * 0.3 : 0
                opacity: view === "settings" ? 0 : 1
                enabled: view === "main"
                Behavior on x { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 0.6 } }
                Behavior on opacity { NumberAnimation { duration: 200 } }

                Column {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 12

                    // cabecalho
                    Item {
                        width: parent.width; height: 40
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 4
                            spacing: 3
                            Txt { text: cfg.name || "Lâmpada"; font.family: displayFont; font.pixelSize: 20; font.weight: Font.DemiBold; font.letterSpacing: -0.4 }
                            Txt { text: subtitle(); color: ink2; font.pixelSize: 12 }
                        }
                        RoundBtn {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            glyph: "gear"
                            onClicked: showView("settings")
                        }
                    }

                    Row {
                        width: parent.width
                        height: parent.height - 40 - 70 - 24
                        spacing: 12

                        // brilho: pilula vertical
                        Glass {
                            id: brightPill
                            width: 96; height: parent.height; radius: 30
                            activeFocusOnTab: true
                            // a parte de baixo de um retangulo arredondado do tamanho da pilula:
                            // cantos de baixo redondos, topo reto, sem textura intermediaria
                            Item {
                                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 1 }
                                height: (brightPill.height - 2) * bright / 100
                                clip: true
                                Behavior on height { enabled: !brightArea.pressed; NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                                Rectangle {
                                    anchors.bottom: parent.bottom
                                    width: parent.width; height: brightPill.height - 2
                                    radius: 29
                                    color: lampOn ? "#ffffff" : Qt.rgba(1, 1, 1, 0.28)
                                }
                            }
                            Icon { name: "sun"; width: 22; height: 22; tint: ink2; anchors.horizontalCenter: parent.horizontalCenter; y: 18 }
                            Txt {
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.bottom: parent.bottom; anchors.bottomMargin: 16
                                text: bright + "<font size='2'>%</font>"
                                textFormat: Text.RichText
                                font.family: roundedFont; font.pixelSize: 22; font.weight: Font.DemiBold
                                color: !lampOn ? ink2 : bright < 18 ? ink : dark
                            }
                            MouseArea {
                                id: brightArea
                                anchors.fill: parent
                                cursorShape: Qt.SizeVerCursor
                                property real acc: 0
                                function apply(m) { setBright((1 - m.y / height) * 100) }
                                onPressed: m => { brightPill.forceActiveFocus(); apply(m) }
                                onPositionChanged: m => apply(m)
                                onWheel: w => {
                                    acc += (w.angleDelta.y + w.angleDelta.x) / 24  // 120 por clique = 5%
                                    const d = Math.trunc(acc); if (!d) return
                                    acc -= d; setBright(bright + d)
                                }
                            }
                            Keys.onUpPressed: setBright(bright + 5)
                            Keys.onDownPressed: setBright(bright - 5)
                        }

                        Column {
                            width: parent.width - 96 - 12
                            height: parent.height
                            spacing: 12

                            // liga/desliga
                            Glass {
                                id: powerTile
                                width: parent.width
                                height: parent.height - tempTile.height - 12
                                Rectangle {
                                    id: knob
                                    x: 16; y: 16; width: 46; height: 46; radius: 23
                                    color: lampOn ? lampColor : Qt.rgba(1, 1, 1, 0.1)
                                    border.color: Qt.rgba(1, 1, 1, lampOn ? 0.35 : 0.18)
                                    Behavior on color { ColorAnimation { duration: 400 } }
                                    Icon { anchors.centerIn: parent; name: "power"; width: 22; height: 22; stroke: 2.2; tint: lampOn ? dark : ink }
                                    scale: powerArea.pressed ? 0.94 : 1
                                    Behavior on scale { NumberAnimation { duration: 90 } }
                                }
                                Column {
                                    x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 14
                                    spacing: 2
                                    Txt { text: lampOn ? "Acesa" : "Apagada"; font.family: displayFont; font.pixelSize: 17; font.weight: Font.DemiBold }
                                    Txt { text: lampOn ? "Clique para apagar" : "Clique para acender"; color: ink2; font.pixelSize: 12 }
                                }
                                MouseArea {
                                    id: powerArea
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: if (!needConnection()) send(backend.power(!st.on))
                                }
                            }

                            // temperatura
                            Glass {
                                id: tempTile
                                width: parent.width; height: 118
                                Column {
                                    anchors.fill: parent; anchors.margins: 13; anchors.leftMargin: 14; anchors.rightMargin: 14
                                    spacing: 10
                                    Item {
                                        width: parent.width; height: 16
                                        Txt { text: "Branco"; font.weight: Font.DemiBold }
                                        Txt {
                                            anchors.right: parent.right
                                            text: colourMode ? "modo cor" : kelvin(temp) + " K"
                                            color: ink2; font.family: roundedFont; font.pixelSize: 12; font.weight: Font.Medium
                                        }
                                    }
                                    Rectangle {
                                        id: track
                                        width: parent.width; height: 26; radius: 13
                                        activeFocusOnTab: true
                                        gradient: Gradient {
                                            orientation: Gradient.Horizontal
                                            GradientStop { position: 0.0; color: "#ffa94d" }
                                            GradientStop { position: 0.45; color: "#ffd9a8" }
                                            GradientStop { position: 0.7; color: "#f4f1ff" }
                                            GradientStop { position: 1.0; color: "#bcd4ff" }
                                        }
                                        Rectangle {
                                            width: 30; height: 30; radius: 15
                                            anchors.verticalCenter: parent.verticalCenter
                                            x: 13 + (track.width - 26) * temp / 100 - 15
                                            color: Qt.rgba(1, 1, 1, 0.35)
                                            border.color: "white"; border.width: 2
                                            opacity: colourMode ? 0.45 : 1
                                            scale: tempArea.pressed ? 1.08 : 1
                                            Behavior on x { enabled: !tempArea.pressed; NumberAnimation { duration: 120 } }
                                        }
                                        MouseArea {
                                            id: tempArea
                                            anchors.fill: parent
                                            anchors.margins: -6
                                            cursorShape: Qt.SizeHorCursor
                                            property real acc: 0
                                            function apply(m) { setTemp((m.x - 6 - 13) / (track.width - 26) * 100) }
                                            onPressed: m => { track.forceActiveFocus(); apply(m) }
                                            onPositionChanged: m => apply(m)
                                            onWheel: w => {
                                                acc += (w.angleDelta.y - w.angleDelta.x) / 24
                                                const d = Math.trunc(acc); if (!d) return
                                                acc -= d; setTemp(temp + d)
                                            }
                                        }
                                        Keys.onLeftPressed: setTemp(temp - 5)
                                        Keys.onRightPressed: setTemp(temp + 5)
                                    }
                                    Row {
                                        width: parent.width; spacing: 6
                                        Repeater {
                                            model: [["Quente", 0], ["Neutro", 50], ["Frio", 100]]
                                            Rectangle {
                                                required property var modelData
                                                width: (parent.width - 12) / 3; height: 26; radius: 13
                                                color: chipArea.containsMouse ? Qt.rgba(1, 1, 1, 0.14) : Qt.rgba(1, 1, 1, 0.08)
                                                border.color: Qt.rgba(1, 1, 1, 0.1)
                                                Txt { anchors.centerIn: parent; text: modelData[0]; font.pixelSize: 12; color: ink }
                                                MouseArea {
                                                    id: chipArea
                                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                                    onClicked: setTemp(modelData[1])
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // cores
                    Glass {
                        width: parent.width; height: 58
                        Row {
                            anchors.centerIn: parent
                            spacing: (parent.width - 28 - 8 * 30) / 7
                            Repeater {
                                model: [["Vermelho", 0, 100], ["Laranja", 28, 100], ["Amarelo", 50, 100], ["Verde", 125, 100],
                                        ["Azul", 225, 100], ["Roxo", 275, 100], ["Rosa", 320, 85]]
                                Item {
                                    required property var modelData
                                    readonly property bool active: colourMode && Math.min(Math.abs(modelData[1] - hue), 360 - Math.abs(modelData[1] - hue)) < 8
                                                                   && Math.abs(modelData[2] - sat) < 10
                                    width: 30; height: 30
                                    Rectangle {
                                        anchors.centerIn: parent
                                        width: 40; height: 40; radius: 20
                                        color: "transparent"; border.color: Qt.rgba(1, 1, 1, 0.9); border.width: 2
                                        visible: parent.active
                                    }
                                    Rectangle {
                                        id: swatch
                                        readonly property color c: Qt.hsva(modelData[1] / 360, modelData[2] / 100, 1, 1)
                                        anchors.fill: parent; radius: 15
                                        gradient: Gradient {
                                            GradientStop { position: 0.0; color: Qt.lighter(swatch.c, 1.35) }
                                            GradientStop { position: 0.5; color: swatch.c }
                                            GradientStop { position: 1.0; color: Qt.darker(swatch.c, 1.12) }
                                        }
                                        scale: swArea.containsMouse ? 1.1 : 1
                                        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutBack } }
                                    }
                                    MouseArea {
                                        id: swArea
                                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: setColour(modelData[1], modelData[2])
                                    }
                                    ToolTip.visible: swArea.containsMouse
                                    ToolTip.text: modelData[0]
                                    ToolTip.delay: 600
                                }
                            }
                            Image {
                                width: 30; height: 30
                                source: backend.wheelUrl
                                smooth: true; mipmap: true
                                scale: wheelBtn.containsMouse ? 1.1 : 1
                                Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutBack } }
                                MouseArea {
                                    id: wheelBtn
                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: if (!needConnection()) sheetOpen = true
                                }
                            }
                        }
                    }
                }
            }

            // ===== roda de cores =====
            Glass {
                id: sheet
                x: 10; width: parent.width - 20; height: 320
                y: sheetOpen ? parent.height - height - 10 : parent.height + 20
                opacity: sheetOpen ? 1 : 0
                radius: 24
                gradient: Gradient {
                    GradientStop { position: 0.0; color: Qt.rgba(60 / 255, 62 / 255, 72 / 255, 0.96) }
                    GradientStop { position: 1.0; color: Qt.rgba(30 / 255, 31 / 255, 38 / 255, 0.98) }
                }
                Behavior on y { NumberAnimation { duration: 340; easing.type: Easing.OutBack; easing.overshoot: 0.7 } }
                Behavior on opacity { NumberAnimation { duration: 200 } }
                Item {
                    x: 16; y: 14; width: parent.width - 32; height: 30
                    Txt { anchors.verticalCenter: parent.verticalCenter; text: "Cor"; font.family: displayFont; font.pixelSize: 17; font.weight: Font.DemiBold }
                    Btn { anchors.right: parent.right; primary: true; text: "Pronto"; implicitHeight: 30; onClicked: sheetOpen = false }
                }
                Image {
                    id: wheel
                    width: 230; height: 230
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 60
                    source: backend.wheelUrl
                    smooth: true; mipmap: true
                    Rectangle {
                        width: 26; height: 26; radius: 13
                        x: 115 + Math.cos(hue * Math.PI / 180) * sat / 100 * 115 - 13
                        y: 115 + Math.sin(hue * Math.PI / 180) * sat / 100 * 115 - 13
                        color: Qt.hsva(hue / 360, sat / 100, 1, 1)
                        border.color: "white"; border.width: 2
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.CrossCursor
                        function apply(m) {
                            const dx = m.x - width / 2, dy = m.y - height / 2
                            setColour((Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360, Math.min(1, Math.hypot(dx, dy) / (width / 2)) * 100)
                        }
                        onPressed: m => apply(m)
                        onPositionChanged: m => apply(m)
                    }
                }
            }

            // ===== configuracoes =====
            Flickable {
                id: settings
                width: parent.width; height: parent.height
                x: view === "settings" ? 0 : width * 1.05
                opacity: view === "settings" ? 1 : 0
                enabled: view === "settings"
                Behavior on x { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 0.6 } }
                Behavior on opacity { NumberAnimation { duration: 200 } }
                contentHeight: form.height + 36
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                property bool dirty: false
                property string version: "3.5"
                property string qrToken: ""
                property string qrCode: ""
                property double qrDeadline: 0

                function fill() {
                    cfg = backend.getConfig()
                    field(0).text = cfg.name || ""
                    field(1).text = cfg.id || ""
                    field(2).text = cfg.key || ""
                    field(3).text = cfg.ip || ""
                    version = cfg.version || "3.5"
                    autostart.on = !!cfg.autostart
                    dirty = false
                }

                Connections {
                    target: backend
                    function onScanFinished(found) {
                        scanBtn.text = "Procurar na rede"; scanBtn.enabledBtn = true
                        if (!found.length) return toast.show("Nenhuma lâmpada respondeu. Ela está ligada e no mesmo Wi-Fi?", 5000)
                        const d = found.find(f => f.id === settings.field(1).text) || found[0]
                        settings.field(3).text = d.ip
                        if (!settings.field(1).text) settings.field(1).text = d.id
                        if (d.version) settings.version = d.version
                        settings.dirty = true
                        toast.show(found.length > 1 ? found.length + " aparelhos encontrados, usei " + d.ip : "Encontrada em " + d.ip)
                    }
                    function onSmartlifeQr(r) {
                        qrGo.enabledBtn = true
                        if (r.error) return toast.show(r.error, 5000)
                        settings.qrToken = r.token
                        settings.qrDeadline = Date.now() + 4 * 60 * 1000
                        qrImg.source = r.qr
                        toast.show("Escaneie com o Smart Life e confirme no celular", 0)
                        pollTimer.start()
                    }
                    function onSmartlifeResult(r) {
                        if (!settings.qrToken) return
                        if (r.pending) return
                        settings.stopQr()
                        slBox.open = false
                        if (r.error) return toast.show(r.error, 6000)
                        if (!r.devices.length) return toast.show("Nenhum aparelho nessa conta")
                        const d = r.devices[0]
                        settings.field(1).text = d.id; settings.field(2).text = d.key; settings.field(3).text = "Auto"
                        if (!settings.field(0).text || settings.field(0).text === "Lâmpada") settings.field(0).text = d.name
                        settings.dirty = true
                        toast.show("Chave de \"" + d.name + "\" preenchida. Clique em Salvar e conectar.", 5000)
                    }
                }
                function stopQr() { pollTimer.stop(); qrToken = ""; qrImg.source = ""; toast.hide() }
                Timer {
                    id: pollTimer
                    interval: 2000; repeat: true
                    onTriggered: {
                        if (!slBox.open || view !== "settings" || !win.visible) return settings.stopQr()
                        if (Date.now() > settings.qrDeadline) {
                            settings.stopQr()
                            return toast.show("O QR code expirou. Clique em Gerar QR de novo.", 5000)
                        }
                        backend.smartlifePoll(settings.qrToken, settings.qrCode)
                    }
                }

                Column {
                    id: form
                    x: 18; y: 18
                    width: parent.width - 36
                    spacing: 10

                    Item {
                        width: parent.width; height: 40
                        Column {
                            anchors.verticalCenter: parent.verticalCenter; x: 4; spacing: 3
                            Txt { text: "Configurações"; font.family: displayFont; font.pixelSize: 20; font.weight: Font.DemiBold; font.letterSpacing: -0.4 }
                            Txt { text: "Conexão com a lâmpada na sua rede"; color: ink2; font.pixelSize: 12 }
                        }
                        RoundBtn {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            glyph: "back"
                            onClicked: showView("main")
                        }
                    }

                    Glass {
                        width: parent.width; height: fields.height + 8; radius: 18
                        Column {
                            id: fields
                            y: 4; width: parent.width
                            Repeater {
                                id: fieldRep
                                model: [["Nome", "Lâmpada", false], ["ID", "ID do aparelho", false],
                                        ["Chave local", "Pegue com o Smart Life", true], ["IP", "Auto", false]]
                                Item {
                                    required property var modelData
                                    required property int index
                                    property alias input: tf
                                    width: fields.width; height: 38
                                    Rectangle { visible: index > 0; width: parent.width - 28; x: 14; height: 1; color: Qt.rgba(1, 1, 1, 0.07) }
                                    Txt { x: 14; anchors.verticalCenter: parent.verticalCenter; width: 78; text: modelData[0]; color: ink2; font.pixelSize: 12 }
                                    TextField {
                                        id: tf
                                        x: 96; width: parent.width - 96 - 10
                                        anchors.verticalCenter: parent.verticalCenter
                                        placeholderText: modelData[1]
                                        placeholderTextColor: ink3
                                        echoMode: modelData[2] ? TextInput.Password : TextInput.Normal
                                        color: ink; font.family: textFont; font.pixelSize: 13
                                        selectByMouse: true
                                        background: null
                                        leftPadding: 0
                                        onTextEdited: settings.dirty = true
                                    }
                                }
                            }
                            Item {
                                width: fields.width; height: 40
                                Rectangle { width: parent.width - 28; x: 14; height: 1; color: Qt.rgba(1, 1, 1, 0.07) }
                                Txt { x: 14; anchors.verticalCenter: parent.verticalCenter; text: "Versão"; color: ink2; font.pixelSize: 12 }
                                Rectangle {
                                    x: 96; width: parent.width - 96 - 14; height: 28
                                    anchors.verticalCenter: parent.verticalCenter
                                    radius: 9; color: Qt.rgba(0, 0, 0, 0.25)
                                    Row {
                                        anchors.fill: parent; anchors.margins: 2
                                        Repeater {
                                            model: ["3.3", "3.4", "3.5"]
                                            Rectangle {
                                                required property string modelData
                                                width: parent.width / 3; height: parent.height; radius: 7
                                                color: settings.version === modelData ? Qt.rgba(1, 1, 1, 0.2) : "transparent"
                                                Txt { anchors.centerIn: parent; text: modelData; font.pixelSize: 12; color: settings.version === modelData ? ink : ink2 }
                                                MouseArea { anchors.fill: parent; onClicked: { settings.version = modelData; settings.dirty = true } }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Row {
                        width: parent.width; spacing: 8
                        Btn {
                            id: scanBtn
                            width: (parent.width - 8) / 2
                            text: "Procurar na rede"
                            onClicked: { text = "Procurando…"; enabledBtn = false; backend.scan() }
                        }
                        Btn {
                            width: (parent.width - 8) / 2
                            text: "Login Smart Life"
                            onClicked: { slBox.open = !slBox.open; if (slBox.open) qrCodeField.forceActiveFocus() }
                        }
                    }

                    Glass {
                        id: slBox
                        property bool open: false
                        visible: open
                        width: parent.width; radius: 18
                        height: slCol.height + 24
                        Column {
                            id: slCol
                            y: 12; x: 12; width: parent.width - 24; spacing: 8
                            Txt {
                                width: parent.width; wrapMode: Text.WordWrap; color: ink2; font.pixelSize: 12
                                text: "No Smart Life: Eu › engrenagem › Conta e segurança › Código de usuário"
                            }
                            Row {
                                width: parent.width; spacing: 8
                                TextField {
                                    id: qrCodeField
                                    width: parent.width - qrGo.width - 8; height: 30
                                    placeholderText: "Código de usuário"; placeholderTextColor: ink3
                                    color: ink; font.family: textFont; font.pixelSize: 13; selectByMouse: true
                                    background: Rectangle { radius: 8; color: Qt.rgba(0, 0, 0, 0.25) }
                                    onAccepted: qrGo.clicked()
                                }
                                Btn {
                                    id: qrGo
                                    primary: true; text: "Gerar QR"; implicitHeight: 30
                                    onClicked: {
                                        if (!qrCodeField.text.trim()) return qrCodeField.forceActiveFocus()
                                        enabledBtn = false
                                        settings.qrCode = qrCodeField.text.trim()
                                        backend.smartlifeStart(settings.qrCode)
                                    }
                                }
                            }
                            Rectangle {
                                visible: qrImg.source != ""
                                width: 182; height: 182; radius: 12; color: "white"
                                anchors.horizontalCenter: parent.horizontalCenter
                                Image { id: qrImg; anchors.fill: parent; anchors.margins: 6; smooth: false; fillMode: Image.PreserveAspectFit }
                            }
                        }
                    }

                    Glass {
                        width: parent.width; height: 46; radius: 18
                        Txt { x: 14; anchors.verticalCenter: parent.verticalCenter; text: "Iniciar com o sistema" }
                        Rectangle {
                            id: autostart
                            property bool on: false
                            anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                            width: 44; height: 26; radius: 13
                            color: on ? "#34c759" : Qt.rgba(1, 1, 1, 0.16)
                            Behavior on color { ColorAnimation { duration: 200 } }
                            Rectangle {
                                width: 22; height: 22; radius: 11; y: 2; color: "white"
                                x: autostart.on ? 20 : 2
                                Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
                            }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    const want = !autostart.on
                                    autostart.on = backend.setAutostart(want)
                                    if (autostart.on !== want) toast.show("Não consegui mudar a inicialização automática")
                                }
                            }
                        }
                    }

                    Btn {
                        width: parent.width; primary: true; text: "Salvar e conectar"
                        onClicked: {
                            const f = i => fieldRep.itemAt(i).input.text.trim()
                            backend.saveConfig({ name: f(0) || "Lâmpada", id: f(1), key: f(2), ip: f(3) || "Auto", version: settings.version })
                            cfg = backend.getConfig()
                            settings.dirty = false
                            showView("main")
                        }
                    }
                    Txt {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "Fechar o Lume"; color: "#ff6961"; font.pixelSize: 12
                        MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: backend.quit() }
                    }
                }
                // campo i do formulario (o Repeater cria os TextField depois; por isso funcao, nao propriedade)
                function field(i) { return fieldRep.itemAt(i).input }
            }
        }

        // aviso
        Rectangle {
            id: toast
            property string msg: ""
            function show(m, ms) { msg = m; hideTimer.interval = ms === undefined ? 3200 : ms; if (hideTimer.interval) hideTimer.restart(); else hideTimer.stop() }
            function hide() { msg = "" }
            x: 18; width: parent.width - 36
            height: toastText.implicitHeight + 18
            y: msg ? parent.height - height - 14 : parent.height + 10
            opacity: msg ? 1 : 0
            radius: 14
            color: Qt.rgba(50 / 255, 52 / 255, 60 / 255, 0.96)
            border.color: hair
            Behavior on y { NumberAnimation { duration: 250; easing.type: Easing.OutBack } }
            Behavior on opacity { NumberAnimation { duration: 200 } }
            Txt { id: toastText; anchors.centerIn: parent; width: parent.width - 24; text: toast.msg; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 12 }
            Timer { id: hideTimer; onTriggered: toast.msg = "" }
        }
    }

    // entrada ao abrir: sobe e aparece
    ParallelAnimation {
        id: enter
        NumberAnimation { target: enterShift; property: "y"; from: 14; to: 0; duration: 280; easing.type: Easing.OutBack; easing.overshoot: 0.8 }
        NumberAnimation { target: content; property: "opacity"; from: 0; to: 1; duration: 200 }
    }
}
