#include "config.h"

#include <cctype>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <set>
#include <sstream>

namespace rlc {

const double kForkMultiplierMin = 0.5;
const double kForkMultiplierMax = 2.0;

const char* const kRuleNames[5] = {"k0_tracking", "l0_early", "yield_slot",
                                   "garbage_hold", "neighbour_release"};

bool Config::HasRule(const std::string& name) const {
  for (const std::string& rule : rules) {
    if (rule == name) return true;
  }
  return false;
}

namespace {

bool IsDigit(char c) { return c >= '0' && c <= '9'; }

class Parser {
 public:
  Parser(const std::string& text, std::string* error)
      : s_(text), error_(error) {}

  bool Object(std::map<std::string, JsonValue>* out) {
    Space();
    if (!Eat('{')) return Fail("expected '{'");
    Space();
    if (Eat('}')) return End();
    for (;;) {
      std::string key;
      Space();
      if (!String(&key)) return false;
      Space();
      if (!Eat(':')) return Fail("expected ':' after \"" + key + "\"");
      Space();
      JsonValue value;
      if (Peek() == '"') {
        value.is_string = true;
        if (!String(&value.text)) return false;
      } else if (!Number(&value.number)) {
        return Fail("\"" + key + "\" must be a number or a string");
      }
      if (!out->emplace(key, value).second) {
        return Fail("duplicate key \"" + key + "\"");
      }
      Space();
      if (Eat(',')) continue;
      if (Eat('}')) return End();
      return Fail("expected ',' or '}'");
    }
  }

 private:
  char Peek() const { return pos_ < s_.size() ? s_[pos_] : '\0'; }
  bool Eat(char c) {
    if (Peek() != c) return false;
    ++pos_;
    return true;
  }
  void Space() {
    while (Peek() == ' ' || Peek() == '\t' || Peek() == '\n' ||
           Peek() == '\r') {
      ++pos_;
    }
  }
  bool Fail(const std::string& what) {
    *error_ = "config: " + what + " at offset " + std::to_string(pos_);
    return false;
  }
  bool End() {
    Space();
    return pos_ == s_.size() || Fail("trailing text");
  }

  bool String(std::string* out) {
    if (!Eat('"')) return Fail("expected a string");
    for (;;) {
      if (pos_ >= s_.size()) return Fail("unterminated string");
      const char c = s_[pos_++];
      if (c == '"') return true;
      const unsigned char byte = static_cast<unsigned char>(c);
      if (byte < 0x20 || byte == 0x7f)
        return Fail("control character in a string");
      if (byte >= 0x80) {
        if (!Utf8Sequence(byte, out)) return Fail("invalid UTF-8 in a string");
        continue;
      }
      if (c != '\\') {
        out->push_back(c);
        continue;
      }
      switch (pos_ < s_.size() ? s_[pos_++] : '\0') {
        case '"':
          out->push_back('"');
          break;
        case '\\':
          out->push_back('\\');
          break;
        case '/':
          out->push_back('/');
          break;
        case 'b':
          out->push_back('\b');
          break;
        case 'f':
          out->push_back('\f');
          break;
        case 'n':
          out->push_back('\n');
          break;
        case 'r':
          out->push_back('\r');
          break;
        case 't':
          out->push_back('\t');
          break;
        case 'u': {
          // Exactly four hex digits, printable ASCII only: no NUL or other
          // control character can reach a path.
          int code = 0;
          for (int i = 0; i < 4; ++i, ++pos_) {
            const char h = Peek();
            if (!std::isxdigit(static_cast<unsigned char>(h))) {
              return Fail("\\u needs four hex digits");
            }
            code =
                code * 16 + (IsDigit(h) ? h - '0' : std::tolower(h) - 'a' + 10);
          }
          if (code < 0x20 || code >= 0x7f) {
            return Fail("only printable ASCII \\u escapes are accepted");
          }
          out->push_back(static_cast<char>(code));
          break;
        }
        default:
          return Fail("bad escape");
      }
    }
  }

  // A multi-byte UTF-8 sequence whose lead byte was just read: well formed,
  // shortest form, no surrogates, at most U+10FFFF. Copied to out.
  bool Utf8Sequence(unsigned char lead, std::string* out) {
    int more = 0;
    uint32_t code = 0;
    if (lead >= 0xC2 && lead <= 0xDF) {
      more = 1;
      code = lead & 0x1F;
    } else if (lead >= 0xE0 && lead <= 0xEF) {
      more = 2;
      code = lead & 0x0F;
    } else if (lead >= 0xF0 && lead <= 0xF4) {
      more = 3;
      code = lead & 0x07;
    } else {
      return false;
    }
    out->push_back(static_cast<char>(lead));
    for (int i = 0; i < more; ++i) {
      const unsigned char next = static_cast<unsigned char>(Peek());
      if ((next & 0xC0) != 0x80) return false;
      code = (code << 6) | (next & 0x3F);
      out->push_back(static_cast<char>(next));
      ++pos_;
    }
    const uint32_t smallest[] = {0, 0x80, 0x800, 0x10000};
    return code >= smallest[more] && code <= 0x10FFFF &&
           (code < 0xD800 || code > 0xDFFF);
  }

  // JSON's number grammar exactly: no leading '+', no leading zeros, no
  // bare '.', no hex, inf or nan.
  bool Number(double* value) {
    const size_t start = pos_;
    Eat('-');
    if (!Eat('0')) {
      if (!IsDigit(Peek())) return false;
      while (IsDigit(Peek())) ++pos_;
    }
    if (Eat('.')) {
      if (!IsDigit(Peek())) return false;
      while (IsDigit(Peek())) ++pos_;
    }
    if (Peek() == 'e' || Peek() == 'E') {
      ++pos_;
      if (!Eat('+')) Eat('-');
      if (!IsDigit(Peek())) return false;
      while (IsDigit(Peek())) ++pos_;
    }
    const std::string token = s_.substr(start, pos_ - start);
    *value = std::strtod(token.c_str(), nullptr);
    return std::isfinite(*value);
  }

  const std::string& s_;
  std::string* error_;
  size_t pos_ = 0;
};

const std::set<std::string>& KnownKeys() {
  static const std::set<std::string> kKnown = {"m_min",
                                               "m_max",
                                               "k0_min",
                                               "k0_cap",
                                               "epsilon",
                                               "phi_min",
                                               "alpha",
                                               "kappa_d",
                                               "kappa_a",
                                               "mode",
                                               "rules",
                                               "rule_l0_early_read_ratio",
                                               "rule_release_fill",
                                               "rule_garbage_drop",
                                               "k",
                                               "b_max",
                                               "beta_w",
                                               "beta_r",
                                               "beta_s",
                                               "c_w",
                                               "c_f",
                                               "c_blk",
                                               "c_sk",
                                               "c_s",
                                               "q_bar",
                                               "decision_log",
                                               "transition_log",
                                               "setoptions_min_interval_ms"};
  return kKnown;
}

class Reader {
 public:
  Reader(const std::map<std::string, JsonValue>& values, std::string* error)
      : values_(values), error_(error) {}

  bool Has(const char* key) const { return values_.count(key) > 0; }

  bool Number(const char* key, double* out) {
    const auto it = values_.find(key);
    if (it == values_.end())
      return Fail(std::string("missing \"") + key + "\"");
    if (it->second.is_string) {
      return Fail(std::string("\"") + key + "\" must be a number");
    }
    *out = it->second.number;
    return true;
  }

  bool Integer(const char* key, int* out) {
    double value = 0;
    if (!Number(key, &value)) return false;
    if (value != std::floor(value) || value < INT_MIN || value > INT_MAX) {
      return Fail(std::string("\"") + key + "\" must be an integer");
    }
    *out = static_cast<int>(value);
    return true;
  }

  bool String(const char* key, std::string* out) {
    const auto it = values_.find(key);
    if (it == values_.end())
      return Fail(std::string("missing \"") + key + "\"");
    if (!it->second.is_string) {
      return Fail(std::string("\"") + key + "\" must be a string");
    }
    *out = it->second.text;
    return true;
  }

  bool Check(bool ok, const std::string& what) { return ok || Fail(what); }

  bool Fail(const std::string& what) {
    *error_ = "config: " + what;
    return false;
  }

 private:
  const std::map<std::string, JsonValue>& values_;
  std::string* error_;
};

bool ParseRules(const std::string& text, std::vector<std::string>* rules,
                Reader* r) {
  // Split on every comma, so "a," and "a,,b" give an empty (unknown) name.
  size_t start = 0;
  for (;;) {
    const size_t comma = text.find(',', start);
    std::string item = text.substr(
        start, comma == std::string::npos ? std::string::npos : comma - start);
    const size_t first = item.find_first_not_of(' ');
    const size_t last = item.find_last_not_of(' ');
    item =
        first == std::string::npos ? "" : item.substr(first, last - first + 1);
    bool known = false;
    for (const char* name : kRuleNames) known = known || item == name;
    if (!known) return r->Fail("unknown rule \"" + item + "\" in \"rules\"");
    for (const std::string& seen : *rules) {
      if (seen == item) return r->Fail("rule \"" + item + "\" listed twice");
    }
    rules->push_back(item);
    if (comma == std::string::npos) return true;
    start = comma + 1;
  }
}

}  // namespace

bool ParseFlatJson(const std::string& text,
                   std::map<std::string, JsonValue>* out, std::string* error) {
  return Parser(text, error).Object(out);
}

bool ParseConfig(const std::string& text, Config* c, std::string* error) {
  std::map<std::string, JsonValue> values;
  if (!ParseFlatJson(text, &values, error)) return false;
  Reader r(values, error);
  if (!r.String("decision_log", &c->decision_log) ||
      !r.String("transition_log", &c->transition_log)) {
    return false;
  }
  for (const auto& entry : values) {
    if (KnownKeys().count(entry.first) == 0) {
      return r.Fail("unknown key \"" + entry.first + "\"");
    }
  }

  Bounds& b = c->bounds;
  if (!r.Number("m_min", &b.m_min) || !r.Number("m_max", &b.m_max) ||
      !r.Integer("k0_min", &b.k0_min) || !r.Integer("k0_cap", &b.k0_cap) ||
      !r.Number("epsilon", &b.epsilon) || !r.Number("phi_min", &b.phi_min) ||
      !r.Number("alpha", &b.alpha) || !r.Number("kappa_d", &b.kappa_d) ||
      !r.Number("kappa_a", &b.kappa_a) || !r.Number("k", &c->k) ||
      !r.Number("b_max", &c->b_max) || !r.Number("beta_w", &c->beta_w) ||
      !r.Number("beta_r", &c->beta_r) || !r.Number("beta_s", &c->beta_s) ||
      !r.Number("c_w", &c->c_w) || !r.Number("c_f", &c->c_f) ||
      !r.Number("c_blk", &c->c_blk) || !r.Number("c_sk", &c->c_sk) ||
      !r.Number("c_s", &c->c_s) || !r.Number("q_bar", &c->q_bar) ||
      !r.Number("setoptions_min_interval_ms", &c->setoptions_min_interval_ms) ||
      !r.String("mode", &c->mode_name)) {
    return false;
  }
  // m = 1 is the fallback and the start, so it must be admissible, and the
  // fork refuses any multiplier outside its own bounds: a config wider than
  // them would make every Apply fail mid-run.
  if (!r.Check(b.m_min <= 1 && b.m_max >= 1, "need m_min <= 1 <= m_max") ||
      !r.Check(b.m_min >= kForkMultiplierMin && b.m_max <= kForkMultiplierMax,
               "m_min and m_max must lie within the fork's [0.5, 2.0] "
               "(ColumnFamilyData::ValidateOptions)") ||
      // A-Impl-5: SetOptions calls are capped.
      !r.Check(c->setoptions_min_interval_ms > 0,
               "need setoptions_min_interval_ms > 0") ||
      !r.Check(b.epsilon > 0 && b.epsilon < 1, "need 0 < epsilon < 1") ||
      // Compact then lands at or above m_min (Pathway A §2, plan §3).
      !r.Check(b.phi_min >= b.m_min * (1 + b.epsilon),
               "need phi_min >= m_min * (1 + epsilon)") ||
      !r.Check(b.alpha > 1, "need alpha > 1") ||
      !r.Check(b.kappa_d > 0 && b.kappa_a > 0, "need kappa_d, kappa_a > 0") ||
      // A-Impl-6: the trigger range is [2, K_cap].
      !r.Check(b.k0_min >= 2 && b.k0_min <= b.k0_cap,
               "need 2 <= k0_min <= k0_cap") ||
      !r.Check(c->k > 0, "need k > 0") ||
      !r.Check(c->b_max > 0, "need b_max > 0") ||
      !r.Check(c->beta_w > 0 && c->beta_r > 0 && c->beta_s > 0,
               "need beta_w, beta_r, beta_s > 0") ||
      // D §1: c_s > 0 always.
      !r.Check(c->c_w > 0 && c->c_s > 0, "need c_w > 0 and c_s > 0") ||
      !r.Check(c->c_f >= 0 && c->c_blk >= 0 && c->c_sk >= 0,
               "need c_f, c_blk, c_sk >= 0") ||
      !r.Check(c->q_bar > 0, "need q_bar > 0")) {
    return false;
  }

  if (c->mode_name == "hold-only") {
    c->mode = Mode::kHoldOnly;
  } else if (c->mode_name == "rules") {
    c->mode = Mode::kRules;
  } else if (c->mode_name == "prior-only" || c->mode_name == "learned" ||
             c->mode_name == "remote-inference") {
    return r.Fail("mode \"" + c->mode_name +
                  "\" is not built yet (plan §7 step 10)");
  } else {
    return r.Fail("unknown mode \"" + c->mode_name + "\"");
  }

  if (r.Has("rules")) {
    std::string names;
    if (!r.String("rules", &names) || !ParseRules(names, &c->rules, &r)) {
      return false;
    }
  }
  if (c->mode == Mode::kRules &&
      !r.Check(!c->rules.empty(), "rules mode needs \"rules\"")) {
    return false;
  }
  if (c->HasRule("l0_early") &&
      (!r.Number("rule_l0_early_read_ratio", &c->rule_l0_early_read_ratio) ||
       !r.Check(c->rule_l0_early_read_ratio > 0,
                "need rule_l0_early_read_ratio > 0"))) {
    return false;
  }
  if (c->HasRule("neighbour_release") &&
      (!r.Number("rule_release_fill", &c->rule_release_fill) ||
       !r.Check(c->rule_release_fill > 0 && c->rule_release_fill <= 1,
                "need 0 < rule_release_fill <= 1"))) {
    return false;
  }
  if (c->HasRule("garbage_hold") &&
      (!r.Number("rule_garbage_drop", &c->rule_garbage_drop) ||
       !r.Check(c->rule_garbage_drop > 0 && c->rule_garbage_drop < 1,
                "need 0 < rule_garbage_drop < 1"))) {
    return false;
  }
  return true;
}

bool LoadConfig(const std::string& path, Config* config, std::string* error) {
  std::ifstream file(path, std::ios::binary);
  if (!file) {
    *error = "config: cannot read " + path;
    return false;
  }
  std::stringstream text;
  text << file.rdbuf();
  return ParseConfig(text.str(), config, error);
}

bool CheckConfigAgainstHost(const Config& config, int l0_trigger,
                            int l0_slowdown_trigger, std::string* error) {
  const Bounds& b = config.bounds;
  if (l0_trigger < b.k0_min || l0_trigger > b.k0_cap) {
    *error = "config: the configured L0 trigger " + std::to_string(l0_trigger) +
             " is outside [k0_min, k0_cap]";
    return false;
  }
  if (b.k0_cap > l0_slowdown_trigger - 1) {
    *error = "config: k0_cap must be below the slowdown trigger " +
             std::to_string(l0_slowdown_trigger) + " (A-Impl-6)";
    return false;
  }
  return true;
}

}  // namespace rlc
