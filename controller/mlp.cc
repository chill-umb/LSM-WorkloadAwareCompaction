#include "mlp.h"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iterator>

namespace rlc {

namespace {

constexpr char kMagic[8] = {'R', 'L', 'C', 'W', '0', '0', '0', '1'};
constexpr size_t kHeader = 32;
// A guard against absurd sizes in a corrupt file.
constexpr uint64_t kMaxPayload = 256ull * 1024 * 1024;
constexpr uint32_t kMaxDim = 1u << 16;

uint64_t ReadU64(const unsigned char* p) {
  uint64_t v = 0;
  for (int i = 7; i >= 0; --i) v = (v << 8) | p[i];
  return v;
}

// Sequential little-endian reads over the payload; any overrun fails.
class Cursor {
 public:
  Cursor(const std::vector<unsigned char>& data) : d_(data) {}
  bool U32(uint32_t* v) {
    if (pos_ + 4 > d_.size()) return false;
    *v = 0;
    for (int i = 3; i >= 0; --i) *v = (*v << 8) | d_[pos_ + i];
    pos_ += 4;
    return true;
  }
  bool I32(int32_t* v) {
    uint32_t u = 0;
    if (!U32(&u)) return false;
    std::memcpy(v, &u, sizeof(u));
    return true;
  }
  bool U64(uint64_t* v) {
    if (pos_ + 8 > d_.size()) return false;
    *v = ReadU64(&d_[pos_]);
    pos_ += 8;
    return true;
  }
  bool F64(double* v) {
    uint64_t u = 0;
    if (!U64(&u)) return false;
    std::memcpy(v, &u, sizeof(u));
    return std::isfinite(*v);
  }
  bool F64s(size_t n, std::vector<double>* out) {
    if (n > (d_.size() - pos_) / 8) return false;
    out->resize(n);
    for (size_t i = 0; i < n; ++i) {
      if (!F64(&(*out)[i])) return false;
    }
    return true;
  }
  bool done() const { return pos_ == d_.size(); }

 private:
  const std::vector<unsigned char>& d_;
  size_t pos_ = 0;
};

LoadStatus Open(const std::string& path, std::ifstream* file,
                std::string* error) {
  file->open(path, std::ios::binary);
  if (!*file) {
    const bool missing = errno == ENOENT;
    *error = "weights: cannot read " + path + ": " + strerror(errno);
    return missing ? LoadStatus::kMissing : LoadStatus::kInvalid;
  }
  return LoadStatus::kOk;
}

LoadStatus ReadHeader(std::ifstream* file, uint64_t* version,
                      uint64_t* length, uint64_t* checksum,
                      std::string* error) {
  unsigned char header[kHeader];
  if (!file->read(reinterpret_cast<char*>(header), kHeader)) {
    *error = "weights: truncated header";
    return LoadStatus::kInvalid;
  }
  if (std::memcmp(header, kMagic, sizeof(kMagic)) != 0) {
    *error = "weights: bad magic";
    return LoadStatus::kInvalid;
  }
  *version = ReadU64(header + 8);
  *length = ReadU64(header + 16);
  *checksum = ReadU64(header + 24);
  if (*version == 0 || *length > kMaxPayload) {
    *error = "weights: bad version or length";
    return LoadStatus::kInvalid;
  }
  return LoadStatus::kOk;
}

bool AgentFrom(uint32_t code, Agent* agent) {
  switch (code) {
    case 0:
      *agent = Agent::kL0;
      return true;
    case 1:
      *agent = Agent::kInterior;
      return true;
    case 2:
      *agent = Agent::kLast;
      return true;
    default:
      return false;
  }
}

bool ReadModel(Cursor* c, Model* m, std::string* why) {
  uint32_t agent = 0, n_layers = 0, has_delta = 0;
  int32_t level = 0;
  if (!c->U32(&agent) || !AgentFrom(agent, &m->agent) || !c->I32(&level) ||
      !c->U64(&m->features_hash) || !c->U32(&m->n_in) || !c->F64(&m->clip) ||
      !c->U32(&n_layers)) {
    *why = "bad model header";
    return false;
  }
  m->level = level;
  const size_t names = FeatureNames(m->agent).size();
  if (m->features_hash != FeaturesHash(m->agent) || m->n_in != names) {
    *why = std::string("the ") + AgentName(m->agent) +
           " model was trained on other state inputs";
    return false;
  }
  if (!(m->clip > 0) || n_layers == 0 || n_layers > 16) {
    *why = "bad clip or layer count";
    return false;
  }
  uint32_t width = m->n_in;
  for (uint32_t i = 0; i < n_layers; ++i) {
    Layer layer;
    if (!c->U32(&layer.out) || !c->U32(&layer.in) || layer.in != width ||
        layer.out == 0 || layer.out > kMaxDim ||
        !c->F64s(static_cast<size_t>(layer.out) * layer.in, &layer.w) ||
        !c->F64s(layer.out, &layer.b)) {
      *why = "bad layer " + std::to_string(i);
      return false;
    }
    width = layer.out;
    m->layers.push_back(std::move(layer));
  }
  if (width < static_cast<uint32_t>(kNumActions)) {
    *why = "the last layer has fewer outputs than actions";
    return false;
  }
  if (!c->U32(&has_delta) || has_delta > 1) {
    *why = "bad delta flag";
    return false;
  }
  m->has_delta = has_delta == 1;
  if (m->has_delta &&
      (!c->F64s(static_cast<size_t>(kNumActions) * m->n_in, &m->delta_w) ||
       !c->F64s(kNumActions, &m->delta_b))) {
    *why = "bad delta head";
    return false;
  }
  return true;
}

}  // namespace

uint64_t Fnv1a64(const void* data, size_t size) {
  uint64_t h = 1469598103934665603ull;
  const auto* p = static_cast<const unsigned char*>(data);
  for (size_t i = 0; i < size; ++i) {
    h ^= p[i];
    h *= 1099511628211ull;
  }
  return h;
}

uint64_t FeaturesHash(Agent agent) {
  std::string joined;
  for (const std::string& name : FeatureNames(agent)) {
    if (!joined.empty()) joined += ",";
    joined += name;
  }
  return Fnv1a64(joined.data(), joined.size());
}

const Model* Weights::Find(Agent agent, int level) const {
  const Model* any = nullptr;
  for (const Model& m : models) {
    if (m.agent != agent) continue;
    if (m.level == level) return &m;
    if (m.level == -1 && any == nullptr) any = &m;
  }
  return any;
}

LoadStatus PeekVersion(const std::string& path, uint64_t* version,
                       std::string* error) {
  std::ifstream file;
  const LoadStatus opened = Open(path, &file, error);
  if (opened != LoadStatus::kOk) return opened;
  uint64_t length = 0, checksum = 0;
  return ReadHeader(&file, version, &length, &checksum, error);
}

LoadStatus LoadWeights(const std::string& path, Weights* out,
                       std::string* error) {
  std::ifstream file;
  const LoadStatus opened = Open(path, &file, error);
  if (opened != LoadStatus::kOk) return opened;
  uint64_t version = 0, length = 0, checksum = 0;
  const LoadStatus header =
      ReadHeader(&file, &version, &length, &checksum, error);
  if (header != LoadStatus::kOk) return header;
  std::vector<unsigned char> payload(length);
  if (length > 0 &&
      !file.read(reinterpret_cast<char*>(payload.data()),
                 static_cast<std::streamsize>(length))) {
    *error = "weights: truncated payload";
    return LoadStatus::kInvalid;
  }
  if (file.peek() != std::ifstream::traits_type::eof()) {
    *error = "weights: trailing bytes";
    return LoadStatus::kInvalid;
  }
  if (Fnv1a64(payload.data(), payload.size()) != checksum) {
    *error = "weights: checksum mismatch";
    return LoadStatus::kInvalid;
  }
  Cursor c(payload);
  uint32_t n_models = 0;
  if (!c.U32(&n_models) || n_models > 1024) {
    *error = "weights: bad model count";
    return LoadStatus::kInvalid;
  }
  Weights w;
  w.version = version;
  for (uint32_t i = 0; i < n_models; ++i) {
    Model m;
    std::string why;
    if (!ReadModel(&c, &m, &why)) {
      *error = "weights: model " + std::to_string(i) + ": " + why;
      return LoadStatus::kInvalid;
    }
    w.models.push_back(std::move(m));
  }
  if (!c.done()) {
    *error = "weights: payload longer than its models";
    return LoadStatus::kInvalid;
  }
  *out = std::move(w);
  return LoadStatus::kOk;
}

std::array<double, kNumActions> Forward(const Model& model,
                                        const std::vector<double>& state) {
  std::vector<double> x(model.n_in, 0.0);
  for (size_t k = 0; k < x.size() && k < state.size(); ++k) {
    const double v = std::isfinite(state[k]) ? state[k] : 0.0;
    x[k] = std::clamp(v, -model.clip, model.clip);
  }
  std::vector<double> h = x, next;
  for (size_t l = 0; l < model.layers.size(); ++l) {
    const Layer& layer = model.layers[l];
    next.assign(layer.out, 0.0);
    for (uint32_t r = 0; r < layer.out; ++r) {
      double sum = layer.b[r];
      const double* row = &layer.w[static_cast<size_t>(r) * layer.in];
      for (uint32_t k = 0; k < layer.in; ++k) sum += row[k] * h[k];
      next[r] = l + 1 < model.layers.size() ? std::max(0.0, sum) : sum;
    }
    h.swap(next);
  }
  std::array<double, kNumActions> f{};
  for (int a = 0; a < kNumActions; ++a) {
    double d = 0;
    if (model.has_delta) {
      d = model.delta_b[a];
      for (uint32_t k = 0; k < model.n_in; ++k) {
        d += model.delta_w[static_cast<size_t>(a) * model.n_in + k] * x[k];
      }
    }
    f[a] = h[a] + d;
  }
  return f;
}

}  // namespace rlc
