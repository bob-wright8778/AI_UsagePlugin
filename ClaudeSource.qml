import QtQuick
import Quickshell.Io

// Shells out to Omarchy's own omarchy-agent-usage-claude collector on
// refresh() and turns its JSON into display-ready properties. A caller never
// sees the collector's JSON shape or that a subprocess is involved at all.
Item {
  id: root
  visible: false

  property bool available: false
  property string tierLabel: ""
  property real sessionPercent: 0
  property string sessionResetAt: ""
  property real weeklyPercent: 0
  property string weeklyResetAt: ""
  property int todayPrompts: 0
  property int todaySessions: 0
  property real todayTotalTokens: 0
  // [{ name, total }], highest total first.
  property var modelRows: []
  // [{ date, messageCount }], oldest first -- as the collector reports it.
  property var recentDays: []

  function refresh() {
    if (!proc.running) proc.running = true
  }

  function findLimit(limits, matcher) {
    for (var i = 0; i < limits.length; i++) {
      if (matcher(String(limits[i].label || "").toLowerCase())) return limits[i]
    }
    return null
  }

  function buildModelRows(usageByModel) {
    var rows = []
    for (var id in usageByModel) {
      var bucket = usageByModel[id] || {}
      var total = Number(bucket.inputTokens || 0) + Number(bucket.outputTokens || 0)
        + Number(bucket.cacheReadInputTokens || 0) + Number(bucket.cacheCreationInputTokens || 0)
      rows.push({ name: id, total: total })
    }
    rows.sort(function(a, b) { return b.total - a.total })
    return rows
  }

  function parse(raw) {
    try {
      var record = JSON.parse(String(raw || ""))
      if (!record || typeof record !== "object") throw new Error("empty collector output")
      var limits = record.limits || []
      var session = findLimit(limits, function(l) { return l.indexOf("session") >= 0 })
      var weekly = findLimit(limits, function(l) { return l.indexOf("week") >= 0 })
      root.sessionPercent = session ? Number(session.percent) : 0
      root.sessionResetAt = session ? String(session.resetsAt || "") : ""
      root.weeklyPercent = weekly ? Number(weekly.percent) : 0
      root.weeklyResetAt = weekly ? String(weekly.resetsAt || "") : ""
      root.todayPrompts = Number(record.todayPrompts || 0)
      root.todaySessions = Number(record.todaySessions || 0)
      root.todayTotalTokens = Number(record.todayTotalTokens || 0)
      root.modelRows = buildModelRows(record.modelUsage || {})
      root.recentDays = record.recentDays || []
      root.tierLabel = String(record.tierLabel || "")
      root.available = true
    } catch (e) {
      root.available = false
    }
  }

  Process {
    id: proc
    command: ["omarchy-agent-usage-claude"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.parse(text)
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.available = false
    }
  }
}
