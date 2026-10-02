#include "log.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <cstring>

namespace rlc {

std::string JsonNumber(double value) {
  if (!std::isfinite(value)) return "null";
  char buffer[32];
  for (int digits : {15, 16, 17}) {
    snprintf(buffer, sizeof(buffer), "%.*g", digits, value);
    if (std::strtod(buffer, nullptr) == value) break;
  }
  return buffer;
}

std::string JsonString(const std::string& text) {
  std::string out = "\"";
  for (const char c : text) {
    if (c == '"' || c == '\\') {
      out.push_back('\\');
      out.push_back(c);
    } else if (c == '\n') {
      out += "\\n";
    } else if (static_cast<unsigned char>(c) < 0x20) {
      char buffer[8];
      snprintf(buffer, sizeof(buffer), "\\u%04x", c);
      out += buffer;
    } else {
      out.push_back(c);
    }
  }
  return out + "\"";
}

JsonLine::JsonLine(const char* type) {
  s_ = "{\"type\":" + JsonString(type) +
       ",\"schema\":" + std::to_string(kLogSchema);
}

void JsonLine::Key(const char* key) { s_ += "," + JsonString(key) + ":"; }

JsonLine& JsonLine::Num(const char* key, double value) {
  Key(key);
  s_ += JsonNumber(value);
  return *this;
}

JsonLine& JsonLine::Uint(const char* key, uint64_t value) {
  Key(key);
  s_ += std::to_string(value);
  return *this;
}

JsonLine& JsonLine::Str(const char* key, const std::string& value) {
  Key(key);
  s_ += JsonString(value);
  return *this;
}

JsonLine& JsonLine::Null(const char* key) {
  Key(key);
  s_ += "null";
  return *this;
}

JsonLine& JsonLine::Nums(const char* key, const std::vector<double>& values) {
  Key(key);
  s_ += "[";
  for (size_t i = 0; i < values.size(); ++i) {
    s_ += (i ? "," : "") + JsonNumber(values[i]);
  }
  s_ += "]";
  return *this;
}

JsonLine& JsonLine::Flags(const char* key, const Mask& mask) {
  Key(key);
  s_ += "[";
  for (size_t i = 0; i < mask.size(); ++i)
    s_ += (i ? "," : "") + std::string(mask[i] ? "1" : "0");
  s_ += "]";
  return *this;
}

JsonLine& JsonLine::Features(const char* key,
                             const std::vector<std::string>& names,
                             const std::vector<double>& values) {
  if (values.empty()) return Null(key);
  Key(key);
  s_ += "{";
  for (size_t i = 0; i < names.size() && i < values.size(); ++i) {
    s_ += (i ? "," : "") + JsonString(names[i]) + ":" + JsonNumber(values[i]);
  }
  s_ += "}";
  return *this;
}

JsonLine& JsonLine::Parts(const char* key, const LevelParts& p) {
  const std::pair<const char*, double> fields[] = {
      {"write_bytes", p.write_bytes},
      {"probes", p.probes},
      {"fp_reads", p.fp_reads},
      {"seeks", p.seeks},
      {"hit_reads", p.hit_reads},
      {"reopens", p.reopens},
      {"slot_out_probes", p.slot_out_probes},
      {"slot_out_fp_reads", p.slot_out_fp_reads},
      {"slot_out_seeks", p.slot_out_seeks},
      {"slot_out_reopens", p.slot_out_reopens},
      {"slot_in_probes", p.slot_in_probes},
      {"slot_in_fp_reads", p.slot_in_fp_reads},
      {"slot_in_seeks", p.slot_in_seeks},
      {"slot_in_reopens", p.slot_in_reopens},
      {"ops", p.ops},
      {"gets", p.gets},
      {"scans", p.scans},
      {"writes", p.writes},
      {"user_bytes", p.user_bytes},
      {"busy_ops", p.busy_ops},
      {"inflow_bytes", p.inflow_bytes},
      {"wait_ops", p.wait_ops},
      {"waits", p.waits},
      {"held_byte_ops", p.held_byte_ops}};
  Key(key);
  s_ += "{";
  bool first = true;
  for (const auto& field : fields) {
    s_ += (first ? "" : ",") + JsonString(field.first) + ":" +
          JsonNumber(field.second);
    first = false;
  }
  s_ += "}";
  return *this;
}

std::string DecisionLine(const DecisionRecord& r) {
  return JsonLine("decision")
      .Uint("id", r.id)
      .Uint("level", static_cast<uint64_t>(r.level))
      .Str("agent", AgentName(r.agent))
      .Uint("op", r.op)
      .Uint("t_us", r.t_us)
      .Str("mode", r.mode)
      .Uint("weights", 0)  // no weights before step 10
      .Str("action", ActionName(r.action))
      .Str("reason", r.reason)
      .Flags("mask", r.mask)
      .Nums("prior_cost", {r.b.begin(), r.b.end()})
      .Num("old", r.old_value)
      .Num("requested", r.requested)
      .Num("effective", r.effective)
      .Num("anchor", r.anchor)
      .Num(r.agent == Agent::kL0 ? "offset" : "timing", r.timing)
      .str();
}

std::string TransitionLine(const TransitionRecord& r) {
  JsonLine line("transition");
  line.Uint("level", static_cast<uint64_t>(r.level))
      .Uint("id", r.id)
      .Uint("start_op", r.start_op)
      .Uint("end_op", r.end_op)
      .Uint("dn", r.end_op >= r.start_op ? r.end_op - r.start_op : 0)
      .Str("agent", AgentName(r.agent))
      .Features("state", FeatureNames(r.agent), r.state);
  if (r.state.empty()) {
    line.Null("mask").Null("prior_cost");
  } else {
    line.Flags("mask", r.mask).Nums("prior_cost", {r.b.begin(), r.b.end()});
  }
  if (r.has_action) {
    line.Str("action", ActionName(r.action));
  } else {
    line.Null("action");
  }
  line.Parts("cost", r.parts)
      .Num("b_bytes", r.b_bytes)
      .Num("rho_tilde", r.rho_tilde)
      .Str("next_agent", AgentName(r.next_agent))
      .Features("next_state", FeatureNames(r.next_agent), r.next_state);
  if (r.next_state.empty()) {
    line.Null("next_mask");
  } else {
    line.Flags("next_mask", r.next_mask);
  }
  return line.Uint("valid", r.valid ? 1 : 0).Str("invalid", r.invalid).str();
}

LogFile::~LogFile() {
  if (file_ != nullptr) fclose(file_);
}

bool LogFile::Open(const std::string& path, std::string* error) {
  file_ = fopen(path.c_str(), "w");
  if (file_ == nullptr) {
    *error = "cannot create " + path + ": " + strerror(errno);
    return false;
  }
  return true;
}

void LogFile::Write(const std::string& line) {
  if (file_ == nullptr) return;
  if (fputs(line.c_str(), file_) == EOF || fputc('\n', file_) == EOF ||
      fflush(file_) != 0) {
    failed_ = true;
  }
}

}  // namespace rlc
