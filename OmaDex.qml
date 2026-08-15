// OmaDex overlay — search every address book at once, then act.
//
// Quickshell ships no generic D-Bus client (only DBusMenu and a fixed set of
// services), so this talks to omadexd's store by spawning the `omadex` CLI in
// its --json mode. Searching is debounced: a process per keystroke would be
// both slow and rude.
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: root

  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  property var shell: null
  property var manifest: null

  property bool opened: false
  property string filterText: ""
  property int selectedIndex: 0
  property bool cursorActive: false
  property bool searching: false
  property string lastError: ""

  // Hover may only drive the selection after the pointer has actually moved.
  // Without this, scrolling the list under a stationary pointer slides a new
  // row under the cursor, which then steals the selection — arrowing past the
  // bottom edge appears to throw the cursor back into the middle.
  property bool pointerActive: false

  // The parsed rows, parallel to the list model. A ListModel cannot hold the
  // address arrays comfortably, and the detail view needs all of them.
  property var people: []
  // Non-null while one contact is expanded. Also the mode switch for the body
  // and for what the keys do.
  property var detail: null
  property var detailRecords: []
  property bool recordsExpanded: false
  property bool recordsLoaded: false

  // Source keys are internal; the labels come from omadex so the two cannot
  // drift apart. "blueferry" is a package name, not something to show someone
  // reading a contact card.
  property var sourceLabels: ({})

  // The settings pane is a third mode alongside the list and one contact.
  property bool settingsOpen: false
  property var sourceSettings: []
  property string storePath: ""
  // True when no source can supply a single contact. Searching an empty
  // address book teaches nobody anything; saying what to install does.
  property bool onboarding: false

  // Browsing the whole address book is paged: 1,200+ people is too many to
  // hold in a model built for a launcher, and a single capped page silently
  // truncates the alphabet at A.
  readonly property int pageSize: 250
  property int listOffset: 0
  property bool listComplete: false
  property bool appending: false

  // The overlay shares the [menu] surface tokens, so any Omarchy theme that
  // styles the menu styles OmaDex too.
  property color background: Color.menu.background
  property color foreground: Color.menu.text
  property color border: Color.menu.border
  property var borderSpec: Border.surfaceSpec("menu", "border", border, Math.max(1, Style.space(2)))
  property color scrim: Color.menu.scrim
  property color selectedBackground: Color.menu.selectedBackground
  property color selectedText: Color.menu.selectedText
  readonly property int cornerRadius: Style.cornerRadius
  property string fontFamily: Style.font.menuFamily
  property int contentMargin: Style.spacing.panelPadding
  property int headerHeight: Math.max(Style.space(34), Style.font.title + Style.spacing.controlPaddingY * 2)
  property int footerHeight: Math.max(Style.space(20), Style.font.bodySmall + Style.spacing.controlPaddingY)
  property int contentSpacing: Style.spacing.md
  property int rowHeight: Math.max(Style.space(46), Style.font.body * 3)
  property int cardWidth: Math.min(Style.space(560), panel.width - Style.gapsOut * 2)
  property int cardHeight: Math.min(Style.space(520), panel.height - Style.gapsOut * 2)

  function open(payloadJson) {
    root.opened = true
    root.filterText = ""
    root.selectedIndex = 0
    root.cursorActive = false
    root.lastError = ""
    root.detail = null
    root.loadSettings()
    root.runQuery("")
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
  }

  function close() {
    root.opened = false
  }

  function dismiss() {
    root.opened = false
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide((root.manifest && root.manifest.id) || "io.github.peteonrails.omadex")
  }

  function toggle() {
    if (root.opened) root.dismiss()
    else root.open("{}")
  }

  function setFilter(nextFilter) {
    root.filterText = nextFilter
    root.selectedIndex = 0
    debounce.restart()
  }

  // An empty query lists the first page rather than showing nothing, so the
  // overlay is useful before you have typed anything.
  function runQuery(query) {
    if (search.running) search.running = false
    root.appending = false
    root.listOffset = 0
    root.listComplete = query.length > 0   // a search returns all it will ever return
    // env resolves the binary against the PATH set on the process below, so a
    // dev checkout in ~/.local/bin and a packaged /usr/bin both work. The
    // query stays a separate argv entry — never interpolated into a shell.
    search.command = query.length > 0
      ? ["/usr/bin/env", "omadex", "--json", "search", query]
      : ["/usr/bin/env", "omadex", "--json", "list",
         "--limit", String(root.pageSize), "--offset", "0"]
    root.searching = true
    search.running = true
  }

  // Fetch the next page while browsing. Never during a search, never twice at
  // once, and never once the end has been seen.
  function loadMore() {
    if (root.filterText.length > 0 || root.searching || root.listComplete) return
    if (search.running) return
    root.appending = true
    root.searching = true
    search.command = ["/usr/bin/env", "omadex", "--json", "list",
                      "--limit", String(root.pageSize),
                      "--offset", String(root.listOffset)]
    search.running = true
  }

  function applyResults(raw) {
    root.searching = false
    var parsed
    try {
      parsed = JSON.parse(raw)
    } catch (error) {
      root.lastError = "omadex returned unreadable output"
      results.clear()
      return
    }
    root.lastError = ""
    if (parsed.labels) root.sourceLabels = parsed.labels
    var rows = parsed.results || []
    if (root.appending) {
      root.people = root.people.concat(rows)
    } else {
      results.clear()
      root.people = rows
    }
    root.listOffset += rows.length
    if (rows.length < root.pageSize) root.listComplete = true
    root.appending = false

    for (var i = 0; i < rows.length; i++) {
      var person = rows[i]
      results.append({
        name: person.name || "(no name)",
        primary: root.primaryAddress(person),
        detail: root.detailLine(person),
        sources: root.labelledSources(person.sources).join(" · "),
        emailCount: (person.emails || []).length,
        phoneCount: (person.phones || []).length
      })
    }
    root.cursorActive = results.count > 0
    if (root.selectedIndex >= results.count) root.selectedIndex = Math.max(0, results.count - 1)
    Qt.callLater(function () {
      if (results.count > 0) resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
    })
  }

  // A phone is the more actionable default on a desktop paired to a phone;
  // email is the fallback when there is no number.
  function primaryAddress(person) {
    var phones = person.phones || []
    var emails = person.emails || []
    if (phones.length > 0) return phones[0]
    if (emails.length > 0) return emails[0]
    return ""
  }

  function sourceLabel(source) {
    return root.sourceLabels[source] || source
  }

  function labelledSources(list) {
    var out = []
    for (var i = 0; i < (list || []).length; i++) out.push(root.sourceLabel(list[i]))
    return out
  }

  function bare(key) {
    var index = key.indexOf(":")
    return index < 0 ? key : key.substring(index + 1)
  }

  function detailLine(person) {
    var parts = []
    var phones = person.phones || []
    var emails = person.emails || []
    if (phones.length > 0) parts.push(root.bare(phones[0]))
    if (emails.length > 0) parts.push(root.bare(emails[0]))
    var extra = phones.length + emails.length - parts.length
    if (extra > 0) parts.push("+" + extra + " more")
    return parts.join("   ")
  }

  function select(delta) {
    if (results.count === 0) return
    root.pointerActive = false
    if (!root.cursorActive) {
      root.cursorActive = true
      root.selectedIndex = delta < 0 ? results.count - 1 : 0
    } else {
      var next = root.selectedIndex + delta
      if (next >= results.count && !root.listComplete) {
        // More of the alphabet exists; fetch it rather than wrapping to A.
        root.selectedIndex = results.count - 1
        root.loadMore()
      } else {
        root.selectedIndex = (next + results.count) % results.count
      }
    }
    root.maybeLoadMore()
    resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
  }

  // Fetch the next page before the cursor reaches the end, so browsing does
  // not stall on a process spawn at every page boundary.
  function maybeLoadMore() {
    if (root.selectedIndex >= results.count - 25) root.loadMore()
  }

  function selectPage(delta) {
    if (results.count === 0) return
    root.pointerActive = false
    var visibleRows = Math.max(1, Math.floor(resultList.height / root.rowHeight))
    var next = root.selectedIndex + delta * visibleRows
    root.selectedIndex = Math.max(0, Math.min(results.count - 1, next))
    root.cursorActive = true
    root.maybeLoadMore()
    resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
  }

  // Opening a contact is the primary gesture: a search result is a summary,
  // and everything a person has only fits once they are expanded.
  function openDetail(index) {
    if (index < 0 || index >= root.people.length) return
    // Never let this become undefined: the body switches on `detail !== null`,
    // and undefined would pass that test and then fail on every field.
    root.detail = root.people[index] || null
    root.detailRecords = []
    root.recordsExpanded = false
    root.recordsLoaded = false
  }

  function closeDetail() {
    root.detail = null
    root.detailRecords = []
    root.recordsExpanded = false
    root.recordsLoaded = false
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
  }

  // "three sources, nine records" is a claim; expanding it is the evidence.
  // Records are fetched only when asked for — most views never need them.
  function toggleRecords() {
    if (!root.detail) return
    root.recordsExpanded = !root.recordsExpanded
    if (root.recordsExpanded && root.detailRecords.length === 0) {
      if (records.running) records.running = false
      records.command = ["/usr/bin/env", "omadex", "--json", "show", root.detail.key]
      records.running = true
    }
  }

  // Open the application a record came from. omadex resolves what "open"
  // means per source and refuses when it cannot preload the contact.
  function openSource(source) {
    if (!root.detail || !source) return
    // A Process rather than execDetached: the shell's PATH is
    // /usr/share/omarchy/bin:/usr/local/bin and does not include
    // ~/.local/bin, and execDetached cannot be given an environment, so
    // resolving `omadex` there fails with no error anywhere.
    if (opener.running) opener.running = false
    opener.command = ["/usr/bin/env", "omadex", "open",
                      root.detail.key, "--source", source]
    opener.running = true
    root.dismiss()
  }

  // ---- settings -----------------------------------------------------------

  function openSettings() {
    root.settingsOpen = true
    root.detail = null
    root.loadSettings()
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
  }

  function closeSettings() {
    root.settingsOpen = false
    root.runQuery(root.filterText)
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
  }

  function loadSettings() {
    if (settings.running) settings.running = false
    settings.command = ["/usr/bin/env", "omadex", "--json", "sources"]
    settings.running = true
  }

  function applySettings(raw) {
    try {
      var parsed = JSON.parse(raw)
      root.sourceSettings = parsed.sources || []
      root.storePath = parsed.store || ""
      var ready = 0
      for (var i = 0; i < root.sourceSettings.length; i++)
        if (root.sourceSettings[i].state === "ready") ready++
      root.onboarding = ready === 0
      if (parsed.labels) root.sourceLabels = parsed.labels
    } catch (error) {
      root.lastError = "could not read settings"
    }
  }

  // Toggling re-syncs, so the counts shown are the ones that follow from the
  // change rather than the ones that preceded it.
  function toggleSource(key, enabled) {
    if (toggler.running) return
    toggler.command = ["/usr/bin/env", "omadex", "sources",
                       enabled ? "disable" : "enable", key]
    toggler.running = true
  }

  function applyRecords(raw) {
    root.recordsLoaded = true
    try {
      var parsed = JSON.parse(raw)
      if (parsed.labels) root.sourceLabels = parsed.labels
      root.detailRecords = parsed.records || []
    } catch (error) {
      root.detailRecords = []
      root.lastError = "could not read the contributing records"
    }
  }

  function copyAddress(key) {
    if (!key) return
    root.dismiss()
    Quickshell.execDetached(["wl-copy", "--", root.bare(key)])
  }

  function emailAddress(key) {
    if (!key) return
    root.dismiss()
    Quickshell.execDetached(["xdg-open", "mailto:" + root.bare(key)])
  }

  function detailPrimary() {
    if (!root.detail) return ""
    return root.primaryAddress(root.detail)
  }

  function detailEmail() {
    if (!root.detail) return ""
    var emails = root.detail.emails || []
    return emails.length > 0 ? emails[0] : ""
  }

  ListModel { id: results }

  Timer {
    id: debounce
    interval: 90
    onTriggered: root.runQuery(root.filterText)
  }

  Process {
    id: search
    // This map REPLACES the environment rather than extending it, so HOME has
    // to be passed explicitly — without it the child cannot find the state
    // directory. `env` then resolves the binary against this PATH, which is
    // what lets a dev checkout and a packaged install both work.
    environment: ({
      "PATH": Quickshell.env("HOME") + "/.local/bin:/usr/local/bin:/usr/bin:/bin",
      "HOME": Quickshell.env("HOME"),
      "XDG_STATE_HOME": Quickshell.env("XDG_STATE_HOME")
    })
    stdout: StdioCollector {
      onStreamFinished: root.applyResults(text)
    }
    stderr: StdioCollector {
      onStreamFinished: if (text.length > 0) root.lastError = text.split("\n")[0]
    }
  }

  Process {
    id: opener
    environment: ({
      "PATH": Quickshell.env("HOME") + "/.local/bin:/usr/share/omarchy/bin:"
              + "/usr/local/bin:/usr/bin:/bin",
      "HOME": Quickshell.env("HOME"),
      "XDG_STATE_HOME": Quickshell.env("XDG_STATE_HOME"),
      "WAYLAND_DISPLAY": Quickshell.env("WAYLAND_DISPLAY"),
      "XDG_RUNTIME_DIR": Quickshell.env("XDG_RUNTIME_DIR"),
      "XDG_CURRENT_DESKTOP": Quickshell.env("XDG_CURRENT_DESKTOP"),
      "TERMINAL": Quickshell.env("TERMINAL"),
      "DBUS_SESSION_BUS_ADDRESS": Quickshell.env("DBUS_SESSION_BUS_ADDRESS")
    })
    stderr: StdioCollector {
      onStreamFinished: if (text.length > 0) root.lastError = text.split("\n")[0]
    }
  }

  Process {
    id: settings
    environment: ({
      "PATH": Quickshell.env("HOME") + "/.local/bin:/usr/local/bin:/usr/bin:/bin",
      "HOME": Quickshell.env("HOME"),
      "XDG_STATE_HOME": Quickshell.env("XDG_STATE_HOME")
    })
    stdout: StdioCollector { onStreamFinished: root.applySettings(text) }
  }

  Process {
    id: toggler
    environment: ({
      "PATH": Quickshell.env("HOME") + "/.local/bin:/usr/local/bin:/usr/bin:/bin",
      "HOME": Quickshell.env("HOME"),
      "XDG_STATE_HOME": Quickshell.env("XDG_STATE_HOME")
    })
    stdout: StdioCollector { onStreamFinished: root.loadSettings() }
  }

  // Separate from `search` so expanding a contact cannot cancel a query in
  // flight, or have its output land in the results model.
  Process {
    id: records
    environment: ({
      "PATH": Quickshell.env("HOME") + "/.local/bin:/usr/local/bin:/usr/bin:/bin",
      "HOME": Quickshell.env("HOME"),
      "XDG_STATE_HOME": Quickshell.env("XDG_STATE_HOME")
    })
    stdout: StdioCollector {
      onStreamFinished: root.applyRecords(text)
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "omadex-contacts"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    Rectangle {
      anchors.fill: parent
      color: root.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.dismiss()
    }

    BorderSurface {
      id: card
      width: root.cardWidth
      height: root.cardHeight
      radius: root.cornerRadius
      anchors.centerIn: parent
      color: root.background
      borderSpec: root.borderSpec
      padding: root.contentMargin

      MouseArea { anchors.fill: parent; onClicked: {} }

      Item {
        id: keyCatcher
        anchors.fill: parent
        focus: true

        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function (event) {
          // An open contact owns the keyboard: escape steps back to the list
          // rather than closing the overlay, and typing does not filter behind
          // it. No call action — this desktop cannot place a phone call.
          if (root.settingsOpen) {
            if (event.key === Qt.Key_Escape || event.key === Qt.Key_Comma) {
              root.closeSettings()
            }
            event.accepted = true
            return
          }

          if (root.detail !== null) {
            if (event.key === Qt.Key_Escape || event.key === Qt.Key_Left) {
              root.closeDetail()
            } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                       || event.key === Qt.Key_C) {
              root.copyAddress(root.detailPrimary())
            } else if (event.key === Qt.Key_E) {
              root.emailAddress(root.detailEmail())
            } else if (event.key === Qt.Key_S || event.key === Qt.Key_Down
                       || event.key === Qt.Key_Right) {
              root.toggleRecords()
            }
            event.accepted = true
            return
          }

          if (event.key === Qt.Key_Escape) {
            if (root.filterText) root.setFilter("")
            else root.dismiss()
            event.accepted = true
          } else if (event.key === Qt.Key_Comma
                     && (event.modifiers & Qt.ControlModifier)) {
            root.openSettings()
            event.accepted = true
          } else if (Util.editsFilter(event, root.filterText)) {
            root.setFilter(Util.editedFilter(event, root.filterText))
            event.accepted = true
          } else if (event.key === Qt.Key_Up) {
            root.select(-1)
            event.accepted = true
          } else if (event.key === Qt.Key_Down) {
            root.select(1)
            event.accepted = true
          } else if (event.key === Qt.Key_PageUp) {
            root.selectPage(-1)
            event.accepted = true
          } else if (event.key === Qt.Key_PageDown) {
            root.selectPage(1)
            event.accepted = true
          } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                     || event.key === Qt.Key_Right) {
            root.openDetail(root.selectedIndex)
            event.accepted = true
          } else if (event.text && event.text.length === 1
                     && event.text.charCodeAt(0) >= 32 && event.text.charCodeAt(0) !== 127) {
            root.setFilter(root.filterText + event.text)
            event.accepted = true
          }
        }
      }

      Column {
        anchors.fill: parent
        anchors.topMargin: card.contentTopInset
        anchors.rightMargin: card.contentRightInset
        anchors.bottomMargin: card.contentBottomInset
        anchors.leftMargin: card.contentLeftInset
        spacing: root.contentSpacing

        Rectangle {
          width: parent.width
          height: root.headerHeight
          radius: root.cornerRadius
          color: "transparent"

          Text {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: root.settingsOpen ? "Address book sources"
                  : root.detail ? root.detail.name
                  : (root.filterText || "Search contacts…")
            color: root.foreground
            opacity: (root.detail || root.filterText) ? 1 : 0.58
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            elide: Text.ElideRight
          }
        }

        Item {
          width: parent.width
          height: parent.height - root.headerHeight - root.footerHeight
                  - root.contentSpacing * 2

          // ---- settings ---------------------------------------------------
          Flickable {
            anchors.fill: parent
            visible: root.settingsOpen
            clip: true
            contentHeight: settingsColumn.height
            boundsBehavior: Flickable.StopAtBounds

            Column {
              id: settingsColumn
              width: parent.width
              spacing: Style.spacing.sm

              Repeater {
                model: root.settingsOpen ? root.sourceSettings : []

                Rectangle {
                  required property var modelData
                  width: settingsColumn.width
                  height: sourceBody.implicitHeight + Style.spacing.md
                  radius: root.cornerRadius
                  color: sourceHover.containsMouse ? root.selectedBackground : "transparent"

                  MouseArea {
                    id: sourceHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.toggleSource(modelData.key, modelData.enabled)
                  }

                  Column {
                    id: sourceBody
                    width: settingsColumn.width - Style.spacing.md * 2
                    x: Style.spacing.md
                    y: Style.spacing.sm
                    spacing: Style.space(2)

                    Text {
                      text: (modelData.enabled ? "●  " : "○  ") + modelData.label
                            + "   " + modelData.records + " records, "
                            + modelData.people + " people"
                      color: modelData.enabled
                             ? (sourceHover.containsMouse ? root.selectedText : root.foreground)
                             : root.foreground
                      opacity: modelData.enabled ? 1 : 0.45
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                    }

                    Text {
                      width: sourceBody.width
                      text: modelData.description
                      color: root.foreground
                      opacity: 0.5
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      wrapMode: Text.WordWrap
                    }

                    Text {
                      width: sourceBody.width
                      visible: text.length > 0
                      text: modelData.state === "ready" ? ""
                            : (modelData.detail || "") +
                              (modelData.hint ? "\n" + modelData.hint : "")
                      color: root.selectedText
                      opacity: 0.85
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      wrapMode: Text.WordWrap
                    }

                    Repeater {
                      model: modelData.fields || []

                      Text {
                        required property var modelData
                        text: modelData.title + ":  " + modelData.value
                        color: root.foreground
                        opacity: 0.42
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.bodySmall
                      }
                    }
                  }
                }
              }

              Text {
                width: settingsColumn.width
                text: "  database:  " + root.storePath
                      + "\n  edit ~/.config/omadex/settings.json for paths"
                color: root.foreground
                opacity: 0.42
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                topPadding: Style.spacing.md
                leftPadding: Style.spacing.md
                wrapMode: Text.WordWrap
              }
            }
          }

          // ---- one contact, expanded -------------------------------------
          Flickable {
            anchors.fill: parent
            visible: root.detail !== null && !root.settingsOpen
            clip: true
            contentHeight: detailColumn.height
            boundsBehavior: Flickable.StopAtBounds

            Column {
              id: detailColumn
              width: parent.width
              spacing: Style.spacing.sm

              Repeater {
                model: root.detail ? (root.detail.emails || []).concat(root.detail.phones || []) : []

                Rectangle {
                  required property string modelData
                  width: detailColumn.width
                  height: root.rowHeight
                  radius: root.cornerRadius
                  color: addressHover.containsMouse ? root.selectedBackground : "transparent"

                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.md
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.bare(modelData)
                    color: addressHover.containsMouse ? root.selectedText : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }

                  Text {
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.md
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.indexOf("mailto:") === 0 ? "email · click to copy"
                                                             : "phone · click to copy"
                    color: addressHover.containsMouse ? root.selectedText : root.foreground
                    opacity: 0.45
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  MouseArea {
                    id: addressHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.copyAddress(modelData)
                  }
                }
              }

              // A postal address is not a destination OmaDex can act on, but
              // it is the thing most worth copying out of a contact card.
              Repeater {
                model: root.detail ? (root.detail.postal || []) : []

                Rectangle {
                  required property string modelData
                  width: detailColumn.width
                  height: Math.max(root.rowHeight, postalText.implicitHeight
                                   + Style.spacing.md)
                  radius: root.cornerRadius
                  color: postalHover.containsMouse ? root.selectedBackground : "transparent"

                  Text {
                    id: postalText
                    anchors.left: parent.left
                    anchors.right: postalLabel.left
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Style.spacing.md
                    anchors.rightMargin: Style.spacing.sm
                    text: modelData
                    color: postalHover.containsMouse ? root.selectedText : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    wrapMode: Text.WordWrap
                  }

                  Text {
                    id: postalLabel
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.md
                    anchors.verticalCenter: parent.verticalCenter
                    text: "address · click to copy"
                    color: postalHover.containsMouse ? root.selectedText : root.foreground
                    opacity: 0.45
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  MouseArea {
                    id: postalHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    // Copied verbatim, not through bare(): a street address
                    // has no scheme prefix to strip.
                    onClicked: {
                      root.dismiss()
                      Quickshell.execDetached(["wl-copy", "--", modelData])
                    }
                  }
                }
              }

              Text {
                width: detailColumn.width
                visible: root.detail && (root.detail.names || []).length > 1
                text: root.detail
                      ? "also " + (root.detail.names || []).slice(1).join(", ")
                      : ""
                color: root.foreground
                opacity: 0.5
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
                leftPadding: Style.spacing.md
                topPadding: Style.spacing.md
              }

              // The provenance line, and the control that opens it up.
              Rectangle {
                width: detailColumn.width
                height: root.rowHeight
                radius: root.cornerRadius
                color: provenanceHover.containsMouse ? root.selectedBackground : "transparent"

                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: Style.spacing.md
                  anchors.verticalCenter: parent.verticalCenter
                  text: {
                    if (!root.detail) return ""
                    var sources = root.detail.sources || []
                    var plural = sources.length === 1 ? "source" : "sources"
                    var count = root.detail.record_count
                    return (root.recordsExpanded ? "▾  " : "▸  ")
                           + sources.length + " " + plural + ", "
                           + count + (count === 1 ? " record" : " records")
                           + "  ·  " + root.labelledSources(sources).join(", ")
                  }
                  color: provenanceHover.containsMouse ? root.selectedText : root.foreground
                  opacity: 0.75
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                MouseArea {
                  id: provenanceHover
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.toggleRecords()
                }
              }

              // One block per contributing record: what that source actually
              // holds, which is how a wrong merge becomes visible.
              Repeater {
                model: root.recordsExpanded ? root.detailRecords : []

                Rectangle {
                  required property var modelData
                  width: detailColumn.width
                  height: recordBody.implicitHeight + Style.spacing.md
                  radius: root.cornerRadius
                  color: recordHover.containsMouse ? root.selectedBackground : "transparent"

                  MouseArea {
                    id: recordHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.openSource(modelData.source)
                  }

                  Column {
                  id: recordBody
                  width: detailColumn.width
                  leftPadding: Style.spacing.md * 2
                  topPadding: Style.spacing.xs
                  bottomPadding: Style.spacing.xs
                  spacing: Style.space(2)

                  Text {
                    text: "[" + root.sourceLabel(modelData.source) + "]  "
                          + (modelData.name || "(no name)")
                          + (recordHover.containsMouse ? "   ↗ open" : "")
                    color: recordHover.containsMouse ? root.selectedText : root.foreground
                    opacity: 0.85
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Text {
                    width: detailColumn.width - Style.spacing.md * 3
                    visible: text.length > 0
                    text: (modelData.phones || []).concat(modelData.emails || []).join("   ")
                    color: root.foreground
                    opacity: 0.5
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                  }

                  // Its own line: a source may contribute nothing but an
                  // address, and folding it in with the destinations would
                  // leave that record looking empty.
                  Text {
                    width: detailColumn.width - Style.spacing.md * 3
                    visible: text.length > 0
                    text: (modelData.postal || []).join("   ")
                    color: root.foreground
                    opacity: 0.5
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                  }
                  }
                }
              }

              Text {
                width: detailColumn.width
                visible: root.recordsExpanded && root.detailRecords.length === 0
                text: root.recordsLoaded
                      ? "  no records returned for this contact"
                      : "  loading records…"
                color: root.foreground
                opacity: 0.5
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                leftPadding: Style.spacing.md
              }
            }
          }

          // ---- search results --------------------------------------------
          ListView {
            id: resultList
            anchors.fill: parent
            visible: root.detail === null && !root.settingsOpen
            model: results
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            // Flicking to the bottom pages in more, the same as arrowing to it.
            onAtYEndChanged: if (atYEnd) root.loadMore()

            delegate: Rectangle {
              required property int index
              required property string name
              required property string detail
              required property string sources

              readonly property bool hasCursor: root.cursorActive && index === root.selectedIndex

              width: resultList.width
              height: root.rowHeight
              radius: root.cornerRadius
              color: hasCursor ? root.selectedBackground : "transparent"

              Column {
                anchors.left: parent.left
                anchors.right: sourceLabel.left
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.spacing.md
                anchors.rightMargin: Style.spacing.sm
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  text: name
                  color: hasCursor ? root.selectedText : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  text: detail
                  visible: detail.length > 0
                  color: hasCursor ? root.selectedText : root.foreground
                  opacity: 0.62
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }
              }

              Text {
                id: sourceLabel
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.rightMargin: Style.spacing.md
                text: sources
                color: hasCursor ? root.selectedText : root.foreground
                opacity: 0.45
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                // A real pointer movement re-enables hover selection; merely
                // having the list scroll beneath a parked cursor does not.
                onPositionChanged: {
                  root.pointerActive = true
                  root.cursorActive = true
                  root.selectedIndex = index
                }
                onContainsMouseChanged: if (containsMouse && root.pointerActive) {
                  root.cursorActive = true
                  root.selectedIndex = index
                }
                onClicked: {
                  root.cursorActive = true
                  root.selectedIndex = index
                  root.openDetail(index)
                }
              }
            }
          }

          // ---- onboarding -------------------------------------------------
          Flickable {
            anchors.fill: parent
            visible: root.onboarding && !root.settingsOpen && root.detail === null
            clip: true
            contentHeight: onboardingColumn.height
            boundsBehavior: Flickable.StopAtBounds

            Column {
              id: onboardingColumn
              width: parent.width
              spacing: Style.spacing.sm

              Text {
                width: onboardingColumn.width - Style.spacing.md * 2
                x: Style.spacing.md
                text: "No address book is set up yet.\n\nOmaDex reads contacts "
                      + "from other applications rather than storing its own. "
                      + "Set up any one of these and it will appear here."
                color: root.foreground
                opacity: 0.8
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                wrapMode: Text.WordWrap
                topPadding: Style.spacing.md
                bottomPadding: Style.spacing.sm
              }

              Repeater {
                model: root.onboarding ? root.sourceSettings : []

                Column {
                  required property var modelData
                  width: onboardingColumn.width - Style.spacing.md * 2
                  x: Style.spacing.md
                  spacing: Style.space(2)
                  bottomPadding: Style.spacing.sm

                  Text {
                    text: modelData.label + "  ·  " + modelData.description
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Text {
                    width: parent.width
                    visible: text.length > 0
                    text: modelData.hint || modelData.detail || ""
                    color: root.selectedText
                    opacity: 0.8
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                  }
                }
              }

              Text {
                width: onboardingColumn.width - Style.spacing.md * 2
                x: Style.spacing.md
                text: "Then run  omadex sync  — or press ctrl+, to review sources."
                color: root.foreground
                opacity: 0.55
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                topPadding: Style.spacing.md
                wrapMode: Text.WordWrap
              }
            }
          }

          Column {
            anchors.centerIn: parent
            spacing: Style.space(8)
            visible: results.count === 0 && !root.settingsOpen
                     && root.detail === null && !root.onboarding

            Text {
              text: root.lastError ? "󰀦" : "󰀄"
              color: root.selectedText
              opacity: 0.8
              font.family: root.fontFamily
              font.pixelSize: Style.font.displayLarge
              horizontalAlignment: Text.AlignHCenter
              width: parent.width
            }

            Text {
              text: root.lastError ? root.lastError
                    : root.searching ? "Searching…"
                    : root.filterText ? "No contact matches “" + root.filterText + "”"
                    : "No contacts yet — run omadex sync"
              color: root.foreground
              opacity: 0.7
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              horizontalAlignment: Text.AlignHCenter
              width: parent.width
              wrapMode: Text.WordWrap
            }
          }
        }

        Rectangle {
          width: parent.width
          height: root.footerHeight
          color: "transparent"

          Text {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: root.settingsOpen
                  ? "click a source to turn it on or off   esc back"
                  : root.detail
                  ? "click address copy   s sources   click source opens it   esc back"
                  : root.onboarding
                  ? "set up any source above   ctrl+, settings   esc close"
                  : "enter open   click open   ctrl+, settings   esc close"
            color: root.foreground
            opacity: 0.42
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideRight
          }
        }
      }
    }
  }
}
