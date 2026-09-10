import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar icon + popup host for Claude Code and GitHub Copilot (JSI) usage.
// Two switchable tabs, one vendor visible at a time -- mirrors
// omarchy.agents's subscription-switch interaction (h/l/click to switch,
// scroll to cycle), its Panel-as-barWidget-entry-point shape, and its
// hero/section-header/meter-bar dashboard styling (PanelHero,
// PanelSectionHeader, PanelSeparator from qs.Ui; the Meter/DayRow/ModelRow
// shapes below are drawn straight from
// /usr/share/omarchy/shell/plugins/agents/Panel.qml).
Panel {
  id: root
  moduleName: "bwright.ai-usage"
  ipcTarget: "bwright.ai-usage"
  manageIpc: false

  // Bar button text follows the bar's own injected foreground (WidgetButton
  // handles that internally); everything inside the popup -- header, section
  // rows -- uses the popup surface's own text token per spec.md's Theming
  // decision, since a theme can style bar text and popup text differently.
  readonly property color popupText: Color.popups.text
  readonly property color dim: Qt.darker(popupText, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  property string selectedTab: "claude"
  property double nowMs: Date.now()

  // Hardcoded per spec.md's "Refresh interval" decision -- not exposed as a
  // settings-schema option for v1.
  readonly property int refreshIntervalSec: 900

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }
  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  function otherTab(tab) { return tab === "claude" ? "copilot" : "claude" }
  function selectTab(tab) { root.selectedTab = tab }
  function toggleTab() { root.selectedTab = otherTab(root.selectedTab) }

  function refreshNow() {
    claudeSource.refresh()
    copilotSource.refresh()
  }

  function formatDuration(ms) {
    if (!(ms > 0)) return "now"
    var minutes = Math.floor(ms / 60000)
    var hours = Math.floor(minutes / 60)
    var days = Math.floor(hours / 24)
    if (days > 0) return days + "d " + (hours % 24) + "h"
    if (hours > 0) return hours + "h " + (minutes % 60) + "m"
    return Math.max(1, minutes) + "m"
  }

  function resetIn(resetAtIso) {
    if (!resetAtIso) return ""
    var ms = new Date(resetAtIso).getTime() - root.nowMs
    return isFinite(ms) ? formatDuration(ms) : ""
  }

  // Shared by Claude's token-count day/model rows and Copilot's credit-count
  // day rows -- both are just "a count", so one formatter serves both.
  function formatCount(n) {
    var v = Number(n || 0)
    if (v >= 1000000) return (v / 1000000).toFixed(1) + "M"
    if (v >= 1000) return (v / 1000).toFixed(1) + "K"
    return String(Math.round(v))
  }

  // Local calendar date, recomputed from nowMs so a panel left open across
  // midnight moves the "Today" row with the clock.
  function todayDate() {
    var now = new Date(root.nowMs)
    return now.getFullYear()
      + "-" + String(now.getMonth() + 1).padStart(2, "0")
      + "-" + String(now.getDate()).padStart(2, "0")
  }

  function dayLabel(date, today) {
    if (today) return "Today"
    var parsed = new Date(String(date || "") + "T00:00:00")
    if (isNaN(parsed.getTime())) return String(date || "")
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][parsed.getDay()]
  }

  function isToday(date) {
    return String(date || "") === root.todayDate()
  }

  // Adapts a vendor-shaped day record ({date, messageCount} for Claude,
  // {date, creditsUsed} for Copilot) into the generic {date, value} shape
  // DaySection/weekPeak work with -- the one place a field name is looked
  // up by string, so neither of those has to be.
  function toDayValues(days, field) {
    var out = []
    for (var i = 0; i < days.length; i++)
      out.push({ date: days[i].date, value: Number(days[i][field] || 0) })
    return out
  }

  function weekPeak(days) {
    var peak = 0
    for (var i = 0; i < days.length; i++) peak = Math.max(peak, Number(days[i].value || 0))
    return peak
  }

  function claudeBindingPercent() {
    return Math.max(Number(claudeSource.sessionPercent || 0), Number(claudeSource.weeklyPercent || 0))
  }

  // No per-category remaining/entitlement data crosses the collector
  // boundary any more (see CopilotSource.qml) -- this account's categories
  // are all unlimited in practice, so the bar reads as unbounded rather than
  // computing a percentage that never turns out to be less than 100%.
  function summaryLabel() {
    if (root.selectedTab === "copilot") {
      return copilotSource.available ? "Copilot ∞" : "Copilot —"
    }
    if (!claudeSource.available) return "Claude —"
    return "Claude " + Math.round(claudeBindingPercent() * 100) + "%"
  }

  // Mirrors omarchy.agents's heroMeta(): an empty/lowercase tier still reads
  // as something ("Subscription", title-cased) rather than a blank hero line.
  function claudeTierMeta() {
    var tier = String(claudeSource.tierLabel || "")
    if (tier === "") return "Subscription"
    return tier.charAt(0).toUpperCase() + tier.slice(1)
  }

  function limitValueText(percent) {
    return percent >= 0 ? Math.round(percent * 100) + "%" : "—"
  }

  function limitCaptionText(resetAtIso) {
    var left = root.resetIn(resetAtIso)
    return left !== "" ? "Resets in " + left : ""
  }

  function copilotTodaySummaryText() {
    return copilotSource.todayCreditsUsed >= 0
      ? "Today: " + root.formatCount(copilotSource.todayCreditsUsed) + " credits used"
      : ""
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Rounded track showing the percentage of an allowance used. `full`
  // renders a muted, always-full track instead, for a limit with no
  // percentage to show that should still read as a bar.
  component Meter: Item {
    id: meter
    property real value: -1
    property bool full: false
    implicitHeight: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.14))

    Rectangle {
      id: track
      anchors.fill: parent
      radius: height / 2
      color: root.alpha(root.popupText, 0.14)
    }

    Rectangle {
      anchors.left: track.left
      anchors.verticalCenter: track.verticalCenter
      height: track.height
      radius: track.radius
      width: track.width * (meter.full ? 1 : root.clamp(meter.value, 0, 1))
      color: meter.full ? root.alpha(root.popupText, 0.35) : root.popupText

      Behavior on width {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
      }
    }
  }

  // A labelled percentage row: title + value, a meter, a dim caption below.
  // Used by Claude's Session/Weekly limits.
  component MeterRow: Column {
    id: meterRow
    property string title: ""
    property string valueText: ""
    property real meterValue: -1
    property bool meterFull: false
    property string captionText: ""

    spacing: Style.space(6)

    Item {
      width: parent.width
      implicitHeight: Math.max(rowLabel.implicitHeight, rowValue.implicitHeight)

      Text {
        id: rowLabel
        textFormat: Text.PlainText
        text: meterRow.title
        color: root.popupText
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        id: rowValue
        textFormat: Text.PlainText
        text: meterRow.valueText
        color: root.popupText
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    Meter {
      width: parent.width
      value: meterRow.meterValue
      full: meterRow.meterFull
    }

    Text {
      textFormat: Text.PlainText
      width: parent.width
      visible: text !== ""
      text: meterRow.captionText
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  // One row per day: label, bar (scaled to the week's peak), a count. Today
  // is picked out in full foreground so the week reads as a run-up to now.
  // Vendor-agnostic -- the caller supplies the already-formatted date label
  // and the raw count (tokens for Claude, credits for Copilot); this
  // component only knows how to draw a day, not what the number means.
  component DayRow: Item {
    id: dayRow
    property string dateLabel: ""
    property real value: 0
    property real ratio: 0
    property bool today: false

    implicitHeight: Math.max(dayLabelText.implicitHeight, dayValue.implicitHeight) + Style.spacing.sm

    Text {
      id: dayLabelText
      textFormat: Text.PlainText
      text: dayRow.dateLabel
      color: dayRow.today ? root.popupText : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: dayRow.today
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }

    Rectangle {
      id: dayTrack
      anchors.left: dayLabelText.right
      anchors.right: dayValue.left
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      height: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.14))
      radius: height / 2
      color: root.alpha(root.popupText, 0.14)

      Rectangle {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        height: parent.height
        radius: parent.radius
        width: parent.width * root.clamp(dayRow.ratio, 0, 1)
        color: dayRow.today ? root.popupText : root.alpha(root.popupText, 0.55)

        Behavior on width {
          NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
        }
      }
    }

    Text {
      id: dayValue
      textFormat: Text.PlainText
      text: root.formatCount(dayRow.value)
      color: dayRow.today ? root.popupText : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      horizontalAlignment: Text.AlignRight
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }
  }

  // A titled section header + one DayRow per entry, scaled to the week's
  // peak. `days` is the generic {date, value} shape from toDayValues() --
  // shared by Claude's TOKENS BY DAY and Copilot's CREDITS BY DAY, which
  // otherwise only differed in header text and source field name.
  component DaySection: Column {
    id: daySection
    property var days: []
    property string headerText: ""

    visible: daySection.days.length > 0
    width: parent.width
    spacing: Style.spacing.sm

    readonly property real peak: Math.max(1, root.weekPeak(daySection.days))

    PanelSectionHeader {
      width: parent.width
      text: daySection.headerText
      foreground: root.popupText
      fontFamily: root.fontFamily
    }

    Repeater {
      model: daySection.days

      DayRow {
        required property var modelData
        width: daySection.width
        today: root.isToday(modelData.date)
        dateLabel: root.dayLabel(modelData.date, today)
        value: modelData.value
        ratio: modelData.value / daySection.peak
      }
    }
  }

  // Model rows read as a table: the share bar fills the row behind the
  // label instead of stacking under it.
  component ModelRow: Item {
    id: modelRow
    property var row: null
    property real share: 0

    implicitHeight: modelName.implicitHeight + Style.spacing.lg

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius
      color: root.alpha(root.popupText, 0.05)
    }

    Rectangle {
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      width: parent.width * root.clamp(modelRow.share, 0, 1)
      radius: Style.cornerRadius
      color: root.alpha(root.popupText, 0.14)

      Behavior on width {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
      }
    }

    Text {
      id: modelName
      textFormat: Text.PlainText
      text: modelRow.row ? modelRow.row.name : ""
      color: root.popupText
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
      anchors.left: parent.left
      anchors.leftMargin: Style.space(8)
      anchors.right: modelTokens.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
    }

    Text {
      id: modelTokens
      textFormat: Text.PlainText
      text: modelRow.row ? root.formatCount(modelRow.row.total) : ""
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
    }
  }

  // Real vendor marks (assets/claude.svg, assets/github.png) -- a later,
  // explicit user request superseding spec.md's original "no custom SVG
  // marks for v1" decision. claude.svg is the exact asset
  // /usr/share/omarchy/shell/plugins/agents/Panel.qml already ships;
  // github.png is a user-supplied glow-style mark (already dark-background
  // and self-contained, unlike a bare flat mark -- no separate badge/tint
  // needed here).
  component VendorMark: Image {
    width: Style.font.display
    height: Style.font.display
    fillMode: Image.PreserveAspectFit
    smooth: true
  }

  ClaudeSource {
    id: claudeSource
  }

  CopilotSource {
    id: copilotSource
  }

  Timer {
    id: refreshTimer
    interval: root.refreshIntervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refreshNow()
  }

  // Keeps "resets in Xh" honest while the panel sits open; cheap enough to
  // run continuously, but only while there is anyone around to read it.
  Timer {
    interval: 30000
    running: root.opened
    repeat: true
    onTriggered: root.nowMs = Date.now()
  }

  onOpenedChanged: if (opened) root.nowMs = Date.now()

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refreshNow(); return "ok" }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.summaryLabel()
    tooltipText: "Claude / Copilot usage"
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.toggleTab()
      else root.toggle()
    }
    onWheelMoved: function(delta) { root.toggleTab() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(340))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onMoveRequested: function(dx, dy) { if (dx !== 0) root.toggleTab() }
      onActivateRequested: root.refreshNow()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) { if (t === "r" || t === "R") root.refreshNow() }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(12)

        // ---------- Tabs ----------
        Row {
          width: parent.width
          spacing: Style.space(8)

          Repeater {
            model: ["claude", "copilot"]

            Rectangle {
              required property string modelData
              readonly property bool selected: root.selectedTab === modelData
              width: (column.width - Style.space(8)) / 2
              height: tabLabel.implicitHeight + Style.space(16)
              radius: Style.cornerRadius
              color: selected ? Style.selectedFillFor(root.popupText, Color.accent) : "transparent"

              Text {
                id: tabLabel
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: modelData === "claude" ? "Claude" : "Copilot"
                color: root.popupText
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: parent.selected
              }

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.selectTab(modelData)
              }
            }
          }
        }

        PanelSeparator {
          foreground: root.popupText
        }

        // ---------- Claude tab ----------
        Column {
          width: parent.width
          spacing: Style.spacing.md
          visible: root.selectedTab === "claude"

          PanelHero {
            width: parent.width
            title: "Claude Code"
            meta: root.claudeTierMeta()
            foreground: root.popupText
            fontFamily: root.fontFamily
            iconComponent: Component { VendorMark { source: Qt.resolvedUrl("assets/claude.svg") } }
          }

          Text {
            visible: !claudeSource.available
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: "Claude usage unavailable"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.italic: true
          }

          Column {
            width: parent.width
            spacing: Style.spacing.md
            visible: claudeSource.available

            PanelSectionHeader {
              text: "LIMITS"
              foreground: root.popupText
              fontFamily: root.fontFamily
            }

            MeterRow {
              width: parent.width
              title: "Session"
              valueText: root.limitValueText(claudeSource.sessionPercent)
              meterValue: claudeSource.sessionPercent
              captionText: root.limitCaptionText(claudeSource.sessionResetAt)
            }
            MeterRow {
              width: parent.width
              title: "Weekly"
              valueText: root.limitValueText(claudeSource.weeklyPercent)
              meterValue: claudeSource.weeklyPercent
              captionText: root.limitCaptionText(claudeSource.weeklyResetAt)
            }

            PanelSeparator {
              visible: daysSection.visible
              foreground: root.popupText
            }

            DaySection {
              id: daysSection
              headerText: "TOKENS BY DAY"
              days: root.toDayValues(claudeSource.recentDays, "messageCount")
            }

            PanelSeparator {
              visible: modelsSection.visible
              foreground: root.popupText
            }

            Column {
              id: modelsSection
              visible: claudeSource.modelRows.length > 0
              width: parent.width
              spacing: Style.spacing.sm

              PanelSectionHeader {
                width: parent.width
                text: "TOKENS BY MODEL"
                foreground: root.popupText
                fontFamily: root.fontFamily
              }

              Repeater {
                model: claudeSource.modelRows

                ModelRow {
                  required property var modelData
                  width: modelsSection.width
                  row: modelData
                  share: modelData.total / Math.max(1, claudeSource.modelRows[0].total)
                }
              }
            }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: "Today: " + claudeSource.todayPrompts + " prompts · " + claudeSource.todaySessions
                + " sessions · " + root.formatCount(claudeSource.todayTotalTokens) + " tokens"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignHCenter
            }
          }
        }

        // ---------- Copilot tab ----------
        Column {
          width: parent.width
          spacing: Style.spacing.md
          visible: root.selectedTab === "copilot"

          PanelHero {
            width: parent.width
            title: "GitHub Copilot"
            meta: copilotSource.plan
            foreground: root.popupText
            fontFamily: root.fontFamily
            iconComponent: Component { VendorMark { source: Qt.resolvedUrl("assets/github.png") } }
          }

          Text {
            visible: !copilotSource.available
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: "Copilot usage unavailable"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.italic: true
          }

          Column {
            width: parent.width
            spacing: Style.spacing.md
            visible: copilotSource.available

            PanelSectionHeader {
              text: "QUOTA"
              foreground: root.popupText
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: "Resets " + copilotSource.quotaResetDate
              color: root.popupText
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: "Total credits used: " + root.formatCount(copilotSource.totalCreditsUsed)
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            PanelSeparator {
              visible: copilotDaysSection.visible
              foreground: root.popupText
            }

            DaySection {
              id: copilotDaysSection
              headerText: "CREDITS BY DAY"
              days: root.toDayValues(copilotSource.recentDays, "creditsUsed")
            }

            Text {
              width: parent.width
              visible: text !== ""
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: root.copilotTodaySummaryText()
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignHCenter
            }
          }
        }
      }
    }
  }
}
