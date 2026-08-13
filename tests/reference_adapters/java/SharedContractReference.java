import java.io.IOException;
import java.math.BigDecimal;
import java.net.Proxy;
import java.net.ProxySelector;
import java.net.SocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * One-source, JDK 11 reference consumer for the shared portable assertion and
 * http-binding-v1 corpora plus the optional real loopback transport checks.
 */
public final class SharedContractReference {
    private static final Pattern LOOPBACK_ORIGIN = Pattern.compile("http://127\\.0\\.0\\.1:([1-9][0-9]{0,4})");
    private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z_][A-Za-z0-9_.-]*");
    private static final Pattern INPUT_ID = Pattern.compile("INPUT-[A-Za-z0-9_.:-]+");
    private static final Pattern STEP_ID = Pattern.compile("STEP-[A-Za-z0-9_.:-]+");
    private static final Pattern METHOD = Pattern.compile("[A-Z][A-Z0-9_-]*");
    private static final Pattern PLACEHOLDER = Pattern.compile("\\{([A-Za-z_][A-Za-z0-9_.-]*)\\}");
    private static final Pattern HEADER_NAME = Pattern.compile("[!#$%&'*+.^_`|~0-9a-z-]+");
    private static final Pattern DNS_LABEL = Pattern.compile("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?");
    private static final Set<String> RESERVED_HEADERS = setOf(
            "host", "content-length", "transfer-encoding", "connection",
            "content-type", "accept-encoding", "user-agent");
    private static String corpusContext = "UNSCOPED";

    /** Explicitly bypass every ambient/system proxy without relying on null API arguments. */
    private static final ProxySelector NO_PROXY = new ProxySelector() {
        @Override
        public List<Proxy> select(URI uri) {
            if (uri == null) throw new IllegalArgumentException("uri");
            return Collections.singletonList(Proxy.NO_PROXY);
        }

        @Override
        public void connectFailed(URI uri, SocketAddress address, IOException failure) {
            // A direct connection has no proxy endpoint whose failure needs recording.
        }
    };

    private SharedContractReference() { }

    public static void main(String[] args) {
        try {
            Arguments arguments = Arguments.parse(args);
            validateOrigin(arguments.origin);
            validateRetryPolicy();
            List<CorpusResult> corpora = evaluateSharedCorpora(arguments.corpusRoot);
            Report report = runLoopback(arguments.origin, corpora);
            System.out.println(report.json());
            if (!"PASS".equals(report.status)) System.exit(1);
        } catch (CorpusProblem problem) {
            System.out.println(Report.failure(problem.code).json());
            System.exit(1);
        } catch (StableFailure failure) {
            System.out.println(Report.failure(failure.code).json());
            System.exit(1);
        } catch (Exception ignored) {
            System.out.println(Report.failure("JAVA_REFERENCE_ERROR").json());
            System.exit(1);
        }
    }

    private static Report runLoopback(String origin, List<CorpusResult> corpora) throws Exception {
        HttpClient client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .followRedirects(HttpClient.Redirect.NEVER)
                .proxy(NO_PROXY)
                .connectTimeout(Duration.ofSeconds(1))
                .build();
        require(client.version() == HttpClient.Version.HTTP_1_1, "HTTP_VERSION_POLICY");
        require(client.followRedirects() == HttpClient.Redirect.NEVER, "REDIRECT_POLICY");
        require(!client.authenticator().isPresent(), "AUTH_POLICY");
        require(!client.cookieHandler().isPresent(), "COOKIE_POLICY");
        require(client.proxy().isPresent() && client.proxy().get() == NO_PROXY, "PROXY_POLICY");

        List<CaseResult> cases = new ArrayList<CaseResult>();
        List<String> protocolHeaders = new ArrayList<String>();

        try {
            byte[] body = "{\"name\":\"value\"}".getBytes(StandardCharsets.UTF_8);
            HttpResponse<byte[]> response = send(client, origin + "/echo?first=1&second=two", "POST", body);
            require(response.statusCode() == 200, "ECHO_STATUS");
            require(Arrays.equals(response.body(), "{\"echo\":true}".getBytes(StandardCharsets.UTF_8)), "ECHO_BODY");
            collectProtocolHeaders(response, protocolHeaders);
            cases.add(CaseResult.pass("echo"));
        } catch (StableFailure failure) {
            cases.add(CaseResult.fail("echo"));
        }

        try {
            HttpResponse<byte[]> response = send(client, origin + "/redirect", "GET", null);
            require(response.statusCode() == 302, "REDIRECT_STATUS");
            require("/final".equals(singleHeader(response, "location")), "REDIRECT_LOCATION");
            collectProtocolHeaders(response, protocolHeaders);
            cases.add(CaseResult.pass("redirect"));
        } catch (StableFailure failure) {
            cases.add(CaseResult.fail("redirect"));
        }

        try {
            HttpResponse<byte[]> unavailable = send(client, origin + "/retryable", "GET", null);
            HttpResponse<byte[]> throttled = send(client, origin + "/retryable?status=429", "GET", null);
            require(unavailable.statusCode() == 503, "RETRYABLE_503_STATUS");
            require(throttled.statusCode() == 429, "RETRYABLE_429_STATUS");
            collectProtocolHeaders(unavailable, protocolHeaders);
            cases.add(CaseResult.pass("retryable"));
        } catch (StableFailure failure) {
            cases.add(CaseResult.fail("retryable"));
        }

        try {
            HttpResponse<byte[]> response = send(client, origin + "/gzip", "GET", null);
            byte[] raw = response.body();
            require(response.statusCode() == 200, "GZIP_STATUS");
            require("gzip".equalsIgnoreCase(singleHeader(response, "content-encoding")), "GZIP_ENCODING");
            require(raw.length >= 2 && raw[0] == (byte) 0x1f && raw[1] == (byte) 0x8b, "GZIP_DECODED");
            collectProtocolHeaders(response, protocolHeaders);
            cases.add(CaseResult.pass("gzip"));
        } catch (StableFailure failure) {
            cases.add(CaseResult.fail("gzip"));
        }

        try {
            // POST is deliberately non-idempotent in OpenJDK's retry classifier.  Together
            // with disableRetryConnect=true and enableAllMethodRetry=false this excludes
            // both automatic connection retry and abrupt-close request replay.
            send(client, origin + "/close", "POST", new byte[0]);
            cases.add(CaseResult.fail("close"));
        } catch (IOException expected) {
            cases.add(CaseResult.pass("close"));
        } catch (InterruptedException expected) {
            Thread.currentThread().interrupt();
            cases.add(CaseResult.pass("close"));
        }

        Collections.sort(protocolHeaders);
        List<String> unique = new ArrayList<String>();
        for (String name : protocolHeaders) {
            if (unique.isEmpty() || !unique.get(unique.size() - 1).equals(name)) unique.add(name);
        }
        for (CaseResult result : cases) {
            if (!"PASS".equals(result.status)) return Report.failure("CASE_FAILED", cases);
        }
        return Report.pass(cases, corpora, unique);
    }

    private static HttpResponse<byte[]> send(HttpClient client, String target, String method, byte[] body)
            throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(target))
                .timeout(Duration.ofSeconds(1))
                .header("x-user", "alpha")
                .header("accept-encoding", "identity")
                .header("user-agent", "test-skills-http-binding-v1");
        if (body != null && body.length > 0) builder.header("content-type", "application/json");
        HttpRequest.BodyPublisher publisher = body == null
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofByteArray(body);
        return client.send(builder.method(method, publisher).build(), HttpResponse.BodyHandlers.ofByteArray());
    }

    private static String singleHeader(HttpResponse<?> response, String name) throws StableFailure {
        List<String> values = response.headers().allValues(name);
        require(values.size() == 1, "RESPONSE_HEADER");
        return values.get(0);
    }

    private static void collectProtocolHeaders(HttpResponse<?> response, List<String> output) {
        // Content-Length is HTTP framing; it is reported separately and is never
        // mistaken for an application-level default header.
        if (response.headers().firstValue("content-length").isPresent()) output.add("content-length");
    }

    private static void validateRetryPolicy() throws StableFailure {
        require("true".equalsIgnoreCase(System.getProperty("jdk.httpclient.disableRetryConnect")), "RETRY_POLICY");
        require(!Boolean.parseBoolean(System.getProperty("jdk.httpclient.enableAllMethodRetry", "false")), "RETRY_POLICY");
    }

    private static void validateOrigin(String origin) throws StableFailure {
        Matcher matcher = origin == null ? null : LOOPBACK_ORIGIN.matcher(origin);
        if (matcher == null || !matcher.matches()) throw new StableFailure("INVALID_ORIGIN");
        int port;
        try {
            port = Integer.parseInt(matcher.group(1));
        } catch (NumberFormatException error) {
            throw new StableFailure("INVALID_ORIGIN");
        }
        if (port < 1 || port > 65535) throw new StableFailure("INVALID_ORIGIN");
    }

    private static void require(boolean condition, String code) throws StableFailure {
        if (!condition) throw new StableFailure(code);
    }

    private static Set<String> setOf(String... values) {
        return Collections.unmodifiableSet(new LinkedHashSet<String>(Arrays.asList(values)));
    }

    private static boolean exactKeys(Map<String, Object> value, String... names) {
        return value.keySet().equals(new HashSet<String>(Arrays.asList(names)));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object value) {
        if (!(value instanceof Map)) throw new CorpusProblem("CORPUS_INVALID");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> asList(Object value) {
        if (!(value instanceof List)) throw new CorpusProblem("CORPUS_INVALID");
        return (List<Object>) value;
    }

    private static String asString(Object value) {
        if (!(value instanceof String)) throw new CorpusProblem("CORPUS_INVALID");
        return (String) value;
    }

    private static boolean asBoolean(Object value) {
        if (!(value instanceof Boolean)) throw new CorpusProblem("CORPUS_INVALID");
        return ((Boolean) value).booleanValue();
    }

    private static int asInt(Object value) {
        if (!(value instanceof JsonNumber) || !((JsonNumber) value).integer) throw new CorpusProblem("CORPUS_INVALID");
        try {
            return ((JsonNumber) value).decimal.intValueExact();
        } catch (ArithmeticException error) {
            throw new CorpusProblem("CORPUS_INVALID");
        }
    }

    private static void mismatch() {
        throw new CorpusProblem("CORPUS_MISMATCH_" + corpusContext);
    }

    static final class Arguments {
        final String origin;
        final Path corpusRoot;

        Arguments(String origin, Path corpusRoot) {
            this.origin = origin;
            this.corpusRoot = corpusRoot;
        }

        static Arguments parse(String[] args) throws StableFailure {
            String origin = null;
            Path root = null;
            for (int index = 0; index < args.length; index++) {
                if ("--origin".equals(args[index]) && index + 1 < args.length && origin == null) {
                    origin = args[++index];
                } else if ("--corpus-root".equals(args[index]) && index + 1 < args.length && root == null) {
                    root = Path.of(args[++index]);
                } else {
                    throw new StableFailure("INVALID_ARGUMENTS");
                }
            }
            if (origin == null) throw new StableFailure("ORIGIN_REQUIRED");
            if (root == null) throw new StableFailure("CORPUS_ROOT_REQUIRED");
            return new Arguments(origin, root);
        }
    }

    static final class CaseResult {
        final String id;
        final String status;

        CaseResult(String id, String status) {
            this.id = id;
            this.status = status;
        }

        static CaseResult pass(String id) { return new CaseResult(id, "PASS"); }
        static CaseResult fail(String id) { return new CaseResult(id, "FAIL"); }

        String json() {
            return "{\"case_id\":" + MiniJson.quote(id) + ",\"status\":" + MiniJson.quote(status) + "}";
        }
    }

    static final class CorpusResult {
        final int count;
        final String corpus;
        final String status;

        CorpusResult(int count, String corpus) {
            this.count = count;
            this.corpus = corpus;
            this.status = "PASS";
        }

        String json() {
            return "{\"corpus\":" + MiniJson.quote(corpus) + ",\"count\":" + count
                    + ",\"status\":\"PASS\"}";
        }
    }

    static final class Report {
        final String status;
        final String code;
        final List<CaseResult> cases;
        final List<CorpusResult> corpora;
        final List<String> protocolHeaders;

        Report(String status, String code, List<CaseResult> cases,
               List<CorpusResult> corpora, List<String> protocolHeaders) {
            this.status = status;
            this.code = code;
            this.cases = cases;
            this.corpora = corpora;
            this.protocolHeaders = protocolHeaders;
        }

        static Report pass(List<CaseResult> cases, List<CorpusResult> corpora, List<String> headers) {
            return new Report("PASS", null, cases, corpora, headers);
        }

        static Report failure(String code) {
            return failure(code, new ArrayList<CaseResult>());
        }

        static Report failure(String code, List<CaseResult> cases) {
            return new Report("FAIL", code, cases, new ArrayList<CorpusResult>(), new ArrayList<String>());
        }

        String json() {
            StringBuilder output = new StringBuilder("{\"adapter\":\"java\",\"cases\":[");
            appendJsonRows(output, cases);
            output.append(']');
            if (code != null) {
                return output.append(",\"code\":").append(MiniJson.quote(code))
                        .append(",\"status\":\"FAIL\"}").toString();
            }
            output.append(",\"corpora\":[");
            appendJsonRows(output, corpora);
            output.append("],\"protocol_headers\":[");
            for (int index = 0; index < protocolHeaders.size(); index++) {
                if (index > 0) output.append(',');
                output.append(MiniJson.quote(protocolHeaders.get(index)));
            }
            return output.append("],\"status\":\"PASS\"}").toString();
        }

        private static void appendJsonRows(StringBuilder output, List<?> rows) {
            for (int index = 0; index < rows.size(); index++) {
                if (index > 0) output.append(',');
                Object row = rows.get(index);
                output.append(row instanceof CaseResult
                        ? ((CaseResult) row).json()
                        : ((CorpusResult) row).json());
            }
        }
    }

    static final class StableFailure extends Exception {
        private static final long serialVersionUID = 1L;
        final String code;
        StableFailure(String code) { this.code = code; }
    }

    static final class CorpusProblem extends RuntimeException {
        private static final long serialVersionUID = 1L;
        final String code;
        CorpusProblem(String code) { this.code = code; }
    }

    private static List<CorpusResult> evaluateSharedCorpora(Path root) {
        if (root == null) throw new CorpusProblem("CORPUS_ROOT_REQUIRED");
        Path regexPath = root.resolve("tests").resolve("fixtures")
                .resolve("portable-regex-v1").resolve("cases.json");
        Path httpRoot = root.resolve("tests").resolve("fixtures").resolve("http-binding-v1");

        Map<String, Object> combined = readJsonObject(regexPath);
        if (!exactKeys(combined, "portable_regex_v1", "assertion_v1")) {
            throw new CorpusProblem("CORPUS_INVALID");
        }
        List<Object> regexCases = sectionCases(combined, "portable_regex_v1");
        List<Object> assertionCases = sectionCases(combined, "assertion_v1");
        requireCorpusCount(regexCases, 64);
        requireCorpusCount(assertionCases, 23);
        evaluateRegexCorpus(regexCases);
        evaluateAssertionCorpus(assertionCases);

        List<Object> requestCases = loadHttpCorpus(httpRoot.resolve("request-cases.json"), "request", 131);
        List<Object> responseCases = loadHttpCorpus(httpRoot.resolve("response-cases.json"), "response", 47);
        List<Object> phaseCases = loadHttpCorpus(httpRoot.resolve("phase-cases.json"), "phase", 40);
        evaluateRequestCorpus(requestCases);
        evaluateResponseCorpus(responseCases);
        evaluatePhaseCorpus(phaseCases);

        List<CorpusResult> results = new ArrayList<CorpusResult>();
        results.add(new CorpusResult(regexCases.size(), "portable-regex-v1"));
        results.add(new CorpusResult(assertionCases.size(), "assertion-v1"));
        results.add(new CorpusResult(requestCases.size(), "http-request-v1"));
        results.add(new CorpusResult(responseCases.size(), "http-response-v1"));
        results.add(new CorpusResult(phaseCases.size(), "http-phase-v1"));
        return results;
    }

    private static Map<String, Object> readJsonObject(Path path) {
        try {
            if (!Files.isRegularFile(path)) throw new CorpusProblem("CORPUS_UNREADABLE");
            return asMap(MiniJson.parseBytes(Files.readAllBytes(path)));
        } catch (IOException error) {
            throw new CorpusProblem("CORPUS_UNREADABLE");
        } catch (MiniJson.Error error) {
            throw new CorpusProblem("CORPUS_INVALID");
        }
    }

    private static List<Object> sectionCases(Map<String, Object> root, String name) {
        Map<String, Object> section = asMap(root.get(name));
        if (!exactKeys(section, "cases")) throw new CorpusProblem("CORPUS_INVALID");
        return asList(section.get("cases"));
    }

    private static List<Object> loadHttpCorpus(Path path, String kind, int count) {
        Map<String, Object> corpus = readJsonObject(path);
        Set<String> allowed = setOf("contract", "corpus_version", "kind", "cases", "value_encodings");
        if (!allowed.containsAll(corpus.keySet())
                || !corpus.keySet().containsAll(setOf("contract", "corpus_version", "kind", "cases"))
                || !"http-binding-v1".equals(corpus.get("contract"))
                || !"1.0.0".equals(corpus.get("corpus_version"))
                || !kind.equals(corpus.get("kind"))) {
            throw new CorpusProblem("CORPUS_INVALID");
        }
        List<Object> cases = asList(corpus.get("cases"));
        requireCorpusCount(cases, count);
        Set<String> ids = new HashSet<String>();
        for (Object item : cases) {
            String id = asString(asMap(item).get("case_id"));
            if (!ids.add(id)) throw new CorpusProblem("CORPUS_INVALID");
        }
        return cases;
    }

    private static void requireCorpusCount(List<Object> rows, int expected) {
        if (rows.size() != expected) throw new CorpusProblem("CORPUS_CONTRACT_MISMATCH");
    }

    private static void evaluateRegexCorpus(List<Object> rows) {
        for (int index = 0; index < rows.size(); index++) {
            corpusContext = "REGEX_" + index;
            Object item = rows.get(index);
            Map<String, Object> row = asMap(item);
            String pattern = asString(row.get("pattern"));
            String value = asString(row.get("value"));
            boolean valid = asBoolean(row.get("valid"));
            try {
                boolean actual = PortableRegex.fullmatch(pattern, value);
                if (!valid || !row.containsKey("matches") || actual != asBoolean(row.get("matches"))) mismatch();
            } catch (RegexError error) {
                if (valid) mismatch();
            }
        }
    }

    private static void evaluateAssertionCorpus(List<Object> rows) {
        for (int index = 0; index < rows.size(); index++) {
            corpusContext = "ASSERTION_" + index;
            Object item = rows.get(index);
            Map<String, Object> row = asMap(item);
            AssertionOutcome actual = evaluateAssertion(row);
            Map<String, Object> expected = asMap(row.get("result"));
            if (!actual.status.equals(expected.get("status"))
                    || !sameNullable(actual.code, expected.get("code"))
                    || !sameNullable(actual.message, expected.get("message"))) {
                mismatch();
            }
        }
    }

    private static AssertionOutcome evaluateAssertion(Map<String, Object> row) {
        String operator = row.get("operator") instanceof String ? (String) row.get("operator") : null;
        Set<String> operators = setOf("equals", "not_equals", "exists", "not_exists", "contains", "matches",
                "greater_than", "greater_or_equal", "less_than", "less_or_equal", "length_equals", "schema_matches");
        if (!operators.contains(operator)) return AssertionOutcome.error("INVALID_ASSERTION", "Unsupported assertion operator.");

        Map<String, Object> actualSource = asMap(row.get("actual"));
        boolean missing = "missing".equals(actualSource.get("state"));
        if (missing) {
            if ("not_exists".equals(operator)) return AssertionOutcome.passed();
            return AssertionOutcome.failed("ASSERTION_MISSING", "Actual value is MISSING.");
        }
        Object actual = decodeValue(actualSource.get("value"));
        if ("exists".equals(operator)) return AssertionOutcome.passed();
        if ("not_exists".equals(operator)) return AssertionOutcome.failed();

        Map<String, Object> expectedSource = asMap(row.get("expected"));
        if ("matches".equals(operator)) {
            if (!(actual instanceof String)
                    || !"regex".equals(expectedSource.get("kind"))
                    || !"portable-regex-v1".equals(expectedSource.get("dialect"))
                    || !(expectedSource.get("pattern") instanceof String)) {
                return AssertionOutcome.error("INVALID_OPERAND", "matches requires a string and portable-regex-v1 operand.");
            }
            try {
                return PortableRegex.fullmatch((String) expectedSource.get("pattern"), (String) actual)
                        ? AssertionOutcome.passed() : AssertionOutcome.failed();
            } catch (RegexError error) {
                return AssertionOutcome.error("INVALID_PORTABLE_REGEX", "Pattern is not portable-regex-v1.");
            }
        }
        if ("schema_matches".equals(operator)) {
            return AssertionOutcome.error("INVALID_OPERAND", "schema_matches corpus requires an authorized schema context.");
        }
        Object expected = decodeValue(expectedSource.get("value"));
        if ("equals".equals(operator) || "not_equals".equals(operator)) {
            boolean equal = jsonEqual(actual, expected);
            return verdict("equals".equals(operator) ? equal : !equal);
        }
        if ("contains".equals(operator)) {
            if (!(actual instanceof String) || !(expected instanceof String)) {
                return AssertionOutcome.error("INVALID_OPERAND", "contains requires string operands.");
            }
            return verdict(((String) actual).contains((String) expected));
        }
        if (operator.startsWith("greater_") || operator.startsWith("less_")) {
            if (!(actual instanceof JsonNumber) || !(expected instanceof JsonNumber)) {
                return AssertionOutcome.error("INVALID_OPERAND", "Comparison requires finite numeric operands.");
            }
            int comparison = ((JsonNumber) actual).decimal.compareTo(((JsonNumber) expected).decimal);
            if ("greater_than".equals(operator)) return verdict(comparison > 0);
            if ("greater_or_equal".equals(operator)) return verdict(comparison >= 0);
            if ("less_than".equals(operator)) return verdict(comparison < 0);
            return verdict(comparison <= 0);
        }
        if ("length_equals".equals(operator)) {
            if (!(expected instanceof JsonNumber) || !((JsonNumber) expected).integer
                    || ((JsonNumber) expected).decimal.signum() < 0) {
                return AssertionOutcome.error("INVALID_OPERAND", "length_equals requires a collection and non-negative integer.");
            }
            int length;
            if (actual instanceof String) length = ((String) actual).codePointCount(0, ((String) actual).length());
            else if (actual instanceof List) length = ((List<?>) actual).size();
            else if (actual instanceof Map) length = ((Map<?, ?>) actual).size();
            else return AssertionOutcome.error("INVALID_OPERAND", "length_equals requires a collection and non-negative integer.");
            return verdict(BigDecimal.valueOf(length).compareTo(((JsonNumber) expected).decimal) == 0);
        }
        return AssertionOutcome.error("INVALID_ASSERTION", "Unsupported assertion operator.");
    }

    private static AssertionOutcome verdict(boolean value) {
        return value ? AssertionOutcome.passed() : AssertionOutcome.failed();
    }

    private static boolean sameNullable(String actual, Object expected) {
        return actual == null ? expected == null : actual.equals(expected);
    }

    private static boolean jsonEqual(Object left, Object right) {
        if (left instanceof JsonNumber && right instanceof JsonNumber) {
            return ((JsonNumber) left).decimal.compareTo(((JsonNumber) right).decimal) == 0;
        }
        if (left == null || right == null) return left == right;
        if (left instanceof Boolean || right instanceof Boolean) return left.equals(right);
        if (left instanceof String && right instanceof String) return left.equals(right);
        if (left instanceof List && right instanceof List) {
            List<?> a = (List<?>) left;
            List<?> b = (List<?>) right;
            if (a.size() != b.size()) return false;
            for (int index = 0; index < a.size(); index++) if (!jsonEqual(a.get(index), b.get(index))) return false;
            return true;
        }
        if (left instanceof Map && right instanceof Map) {
            Map<?, ?> a = (Map<?, ?>) left;
            Map<?, ?> b = (Map<?, ?>) right;
            if (!a.keySet().equals(b.keySet())) return false;
            for (Object key : a.keySet()) if (!jsonEqual(a.get(key), b.get(key))) return false;
            return true;
        }
        return left.equals(right);
    }

    private static Object decodeValue(Object value) {
        if (value instanceof List) {
            List<Object> result = new ArrayList<Object>();
            for (Object item : asList(value)) result.add(decodeValue(item));
            return result;
        }
        if (!(value instanceof Map)) return value;
        Map<String, Object> source = asMap(value);
        if (exactKeys(source, "$corpus_value")) {
            String encoding = asString(source.get("$corpus_value"));
            if ("nan".equals(encoding)) return NonFinite.NAN;
            if ("positive_infinity".equals(encoding)) return NonFinite.POSITIVE_INFINITY;
            if ("negative_infinity".equals(encoding)) return NonFinite.NEGATIVE_INFINITY;
            throw new CorpusProblem("CORPUS_INVALID");
        }
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> entry : source.entrySet()) result.put(entry.getKey(), decodeValue(entry.getValue()));
        return result;
    }

    static final class AssertionOutcome {
        final String status;
        final String code;
        final String message;

        AssertionOutcome(String status, String code, String message) {
            this.status = status;
            this.code = code;
            this.message = message;
        }

        static AssertionOutcome passed() { return new AssertionOutcome("PASSED", null, null); }
        static AssertionOutcome failed() { return failed("ASSERTION_FAILED", "Assertion evaluated to false."); }
        static AssertionOutcome failed(String code, String message) { return new AssertionOutcome("FAILED", code, message); }
        static AssertionOutcome error(String code, String message) { return new AssertionOutcome("ERROR", code, message); }
    }

    enum NonFinite { NAN, POSITIVE_INFINITY, NEGATIVE_INFINITY }

    static final class RegexError extends Exception {
        private static final long serialVersionUID = 1L;
    }

    static final class PortableRegex {
        private PortableRegex() { }

        static boolean fullmatch(String pattern, String value) throws RegexError {
            validateScalars(value, false);
            RegexNode root = new RegexParser(pattern).parse();
            int[] input = value.codePoints().toArray();
            Set<Integer> starts = new LinkedHashSet<Integer>();
            starts.add(Integer.valueOf(0));
            return root.match(input, starts).contains(Integer.valueOf(input.length));
        }

        private static void validateScalars(String value, boolean pattern) throws RegexError {
            if (value == null) throw new RegexError();
            for (int index = 0; index < value.length(); index++) {
                char unit = value.charAt(index);
                if (Character.isHighSurrogate(unit)) {
                    if (index + 1 >= value.length() || !Character.isLowSurrogate(value.charAt(index + 1))) throw new RegexError();
                    index++;
                } else if (Character.isLowSurrogate(unit)) {
                    throw new RegexError();
                }
                if (pattern && (unit == '\r' || unit == '\n' || unit == '\t')) throw new RegexError();
            }
        }
    }

    interface RegexNode {
        Set<Integer> match(int[] input, Set<Integer> starts);
    }

    static final class AlternativesNode implements RegexNode {
        final List<RegexNode> alternatives;
        AlternativesNode(List<RegexNode> alternatives) { this.alternatives = alternatives; }
        public Set<Integer> match(int[] input, Set<Integer> starts) {
            Set<Integer> result = new LinkedHashSet<Integer>();
            for (RegexNode node : alternatives) result.addAll(node.match(input, starts));
            return result;
        }
    }

    static final class SequenceNode implements RegexNode {
        final List<RegexNode> pieces;
        SequenceNode(List<RegexNode> pieces) { this.pieces = pieces; }
        public Set<Integer> match(int[] input, Set<Integer> starts) {
            Set<Integer> positions = starts;
            for (RegexNode node : pieces) {
                positions = node.match(input, positions);
                if (positions.isEmpty()) break;
            }
            return positions;
        }
    }

    static final class RepeatNode implements RegexNode {
        final Atom atom;
        final int minimum;
        final Integer maximum;
        RepeatNode(Atom atom, int minimum, Integer maximum) {
            this.atom = atom;
            this.minimum = minimum;
            this.maximum = maximum;
        }
        public Set<Integer> match(int[] input, Set<Integer> starts) {
            Set<Integer> current = new LinkedHashSet<Integer>(starts);
            for (int count = 0; count < minimum; count++) current = atom.consume(input, current);
            if (maximum != null && maximum.intValue() == minimum) return current;
            Set<Integer> result = new LinkedHashSet<Integer>(current);
            int remaining = maximum == null ? input.length + 1 : maximum.intValue() - minimum;
            for (int count = 0; count < remaining && !current.isEmpty(); count++) {
                current = atom.consume(input, current);
                int before = result.size();
                result.addAll(current);
                if (maximum == null && result.size() == before) break;
            }
            return result;
        }
    }

    static final class Atom {
        final String kind;
        final int literal;
        final Set<Integer> literals;
        final List<int[]> ranges;

        Atom(String kind, int literal, Set<Integer> literals, List<int[]> ranges) {
            this.kind = kind;
            this.literal = literal;
            this.literals = literals;
            this.ranges = ranges;
        }

        Set<Integer> consume(int[] input, Set<Integer> starts) {
            Set<Integer> result = new LinkedHashSet<Integer>();
            for (Integer boxed : starts) {
                int index = boxed.intValue();
                if (index >= input.length) continue;
                int codePoint = input[index];
                boolean matches = "literal".equals(kind) ? codePoint == literal
                        : "dot".equals(kind) ? codePoint != '\n'
                        : literals.contains(Integer.valueOf(codePoint)) || inRange(codePoint);
                if (matches) result.add(Integer.valueOf(index + 1));
            }
            return result;
        }

        private boolean inRange(int codePoint) {
            for (int[] range : ranges) if (range[0] <= codePoint && codePoint <= range[1]) return true;
            return false;
        }
    }

    static final class RegexParser {
        final int[] pattern;
        int index;

        RegexParser(String source) throws RegexError {
            if (source == null || source.codePointCount(0, source.length()) < 1
                    || source.codePointCount(0, source.length()) > 512) throw new RegexError();
            PortableRegex.validateScalars(source, true);
            this.pattern = source.codePoints().toArray();
        }

        RegexNode parse() throws RegexError {
            RegexNode result = parsePattern(false);
            if (index != pattern.length) throw new RegexError();
            return result;
        }

        RegexNode parsePattern(boolean inGroup) throws RegexError {
            List<RegexNode> alternatives = new ArrayList<RegexNode>();
            alternatives.add(parseAlternative());
            while (peek('|')) {
                index++;
                alternatives.add(parseAlternative());
            }
            if (inGroup) {
                if (!peek(')')) throw new RegexError();
                index++;
            }
            return new AlternativesNode(alternatives);
        }

        RegexNode parseAlternative() throws RegexError {
            List<RegexNode> pieces = new ArrayList<RegexNode>();
            while (index < pattern.length && !peek('|') && !peek(')')) pieces.add(parsePiece());
            if (pieces.isEmpty()) throw new RegexError();
            return new SequenceNode(pieces);
        }

        RegexNode parsePiece() throws RegexError {
            if (peek('(')) {
                index++;
                return parsePattern(true);
            }
            Atom atom = parseAtom();
            int minimum = 1;
            Integer maximum = Integer.valueOf(1);
            if (peek('?')) { index++; minimum = 0; maximum = Integer.valueOf(1); }
            else if (peek('*')) { index++; minimum = 0; maximum = null; }
            else if (peek('+')) { index++; minimum = 1; maximum = null; }
            else if (peek('{')) {
                int[] bound = parseBound();
                minimum = bound[0];
                maximum = Integer.valueOf(bound[1]);
            }
            return new RepeatNode(atom, minimum, maximum);
        }

        Atom parseAtom() throws RegexError {
            if (index >= pattern.length || isOneOf(pattern[index], "|)]?*+{}")) throw new RegexError();
            if (peek('.')) { index++; return new Atom("dot", 0, Collections.<Integer>emptySet(), Collections.<int[]>emptyList()); }
            if (peek('[')) return parseClass();
            if (peek('\\')) {
                index++;
                return new Atom("literal", parseEscape(".|()[]?*+{}\\nrt"), Collections.<Integer>emptySet(), Collections.<int[]>emptyList());
            }
            return new Atom("literal", pattern[index++], Collections.<Integer>emptySet(), Collections.<int[]>emptyList());
        }

        Atom parseClass() throws RegexError {
            index++;
            if (peek('^')) throw new RegexError();
            Set<Integer> literals = new LinkedHashSet<Integer>();
            List<int[]> ranges = new ArrayList<int[]>();
            int count = 0;
            while (true) {
                if (index >= pattern.length) throw new RegexError();
                if (peek(']')) {
                    if (count == 0) throw new RegexError();
                    index++;
                    return new Atom("class", 0, literals, ranges);
                }
                ClassCharacter first = parseClassCharacter();
                if (peek('-')) {
                    if (first.escaped) throw new RegexError();
                    index++;
                    if (index >= pattern.length || peek(']')) throw new RegexError();
                    ClassCharacter second = parseClassCharacter();
                    if (second.escaped || first.value < 0x20 || first.value > 0x7e
                            || second.value < 0x20 || second.value > 0x7e || first.value >= second.value) {
                        throw new RegexError();
                    }
                    ranges.add(new int[] {first.value, second.value});
                } else {
                    literals.add(Integer.valueOf(first.value));
                }
                count++;
            }
        }

        ClassCharacter parseClassCharacter() throws RegexError {
            if (index >= pattern.length || peek(']') || peek('-')) throw new RegexError();
            if (peek('\\')) {
                index++;
                return new ClassCharacter(parseEscape("]\\-nrt"), true);
            }
            return new ClassCharacter(pattern[index++], false);
        }

        int parseEscape(String allowed) throws RegexError {
            if (index >= pattern.length || allowed.indexOf(pattern[index]) < 0) throw new RegexError();
            int escaped = pattern[index++];
            if (escaped == 'n') return '\n';
            if (escaped == 'r') return '\r';
            if (escaped == 't') return '\t';
            return escaped;
        }

        int[] parseBound() throws RegexError {
            index++;
            int minimum = parseUnsigned();
            if (peek('}')) { index++; return new int[] {minimum, minimum}; }
            if (!peek(',')) throw new RegexError();
            index++;
            int maximum = parseUnsigned();
            if (!peek('}')) throw new RegexError();
            index++;
            if (minimum > maximum || maximum > 1000) throw new RegexError();
            return new int[] {minimum, maximum};
        }

        int parseUnsigned() throws RegexError {
            int start = index;
            while (index < pattern.length && pattern[index] >= '0' && pattern[index] <= '9') index++;
            if (start == index || index - start > 1 && pattern[start] == '0') throw new RegexError();
            int value = 0;
            for (int cursor = start; cursor < index; cursor++) value = value * 10 + pattern[cursor] - '0';
            if (value > 1000) throw new RegexError();
            return value;
        }

        boolean peek(int expected) { return index < pattern.length && pattern[index] == expected; }

        static boolean isOneOf(int codePoint, String candidates) {
            return candidates.indexOf(codePoint) >= 0;
        }
    }

    static final class ClassCharacter {
        final int value;
        final boolean escaped;
        ClassCharacter(int value, boolean escaped) { this.value = value; this.escaped = escaped; }
    }

    private static void evaluateRequestCorpus(List<Object> rows) {
        evaluateBindingRows(rows);
    }

    private static void evaluatePhaseCorpus(List<Object> rows) {
        evaluateBindingRows(rows);
    }

    private static void evaluateBindingRows(List<Object> rows) {
        for (int index = 0; index < rows.size(); index++) {
            corpusContext = (rows.size() == 131 ? "REQUEST_" : "PHASE_") + index;
            Object item = rows.get(index);
            Map<String, Object> row = asMap(item);
            ProviderRegistry providers = new ProviderRegistry(asList(row.get("providers")));
            StepOutputs outputs = new StepOutputs(asList(row.get("step_outputs")));
            FakeTransport transport = new FakeTransport(asMap(row.get("transport")));
            Map<String, Object> expected = asMap(row.get("expected"));
            ExecutionError caught = null;
            try {
                AbstractRequest request = HttpSemantics.buildRequest(
                        decodeValue(row.get("operation")), decodeValue(row.get("inputs")), providers, outputs);
                if ("request".equals(expected.get("kind"))) {
                    if (!request.equals(requestFromCorpus(asMap(expected.get("request"))))) mismatch();
                }
                RawResponse response = HttpSemantics.executeOnce(request, transport, timeoutFromCorpus(row.get("timeout_seconds")));
                if ("response".equals(expected.get("kind"))) {
                    if (!response.equals(rawResponseFromCorpus(asMap(expected.get("response")), null))) mismatch();
                } else if (!"request".equals(expected.get("kind")) && !"error".equals(expected.get("kind"))) {
                    throw new CorpusProblem("CORPUS_INVALID");
                }
            } catch (ExecutionError error) {
                caught = error;
            }
            if (caught != null) {
                if (!"error".equals(expected.get("kind")) || !caught.matches(expected)) mismatch();
            } else if ("error".equals(expected.get("kind"))) {
                mismatch();
            }
            if (transport.calls != asInt(expected.get("transport_calls"))) mismatch();
            if (expected.containsKey("provider_calls")
                    && !jsonEqual(providers.calls, decodeValue(expected.get("provider_calls")))) mismatch();
        }
    }

    private static void evaluateResponseCorpus(List<Object> rows) {
        for (int index = 0; index < rows.size(); index++) {
            corpusContext = "RESPONSE_" + index;
            Object item = rows.get(index);
            Map<String, Object> row = asMap(item);
            FakeTransport transport = new FakeTransport(asMap(row.get("transport")));
            Map<String, Object> expected = asMap(row.get("expected"));
            ExecutionError caught = null;
            try {
                RawResponse response = HttpSemantics.executeOnce(
                        requestFromCorpus(asMap(row.get("request"))), transport, new JsonNumber("1"));
                ValueState state = HttpSemantics.observeResponse(response, row.get("source"));
                if ("error".equals(expected.get("kind"))) mismatch();
                if (!"observation".equals(expected.get("kind"))) throw new CorpusProblem("CORPUS_INVALID");
                if ("MISSING".equals(expected.get("state"))) {
                    if (!state.missing) mismatch();
                } else if (!"VALUE".equals(expected.get("state"))
                        || state.missing || !jsonEqual(state.value, decodeValue(expected.get("value")))) {
                    mismatch();
                }
            } catch (ExecutionError error) {
                caught = error;
            }
            if (caught != null) {
                if (!"error".equals(expected.get("kind")) || !caught.matches(expected)) mismatch();
            }
            if (transport.calls != asInt(expected.get("transport_calls"))) mismatch();
        }
    }

    private static AbstractRequest requestFromCorpus(Map<String, Object> value) {
        return new AbstractRequest(
                asString(value.get("method")),
                asString(value.get("absolute_url")),
                headersFromCorpus(value.get("ordered_headers")),
                decodeBytes(value.get("body_bytes")));
    }

    private static RawResponse rawResponseFromCorpus(Map<String, Object> value, Map<String, Object> replacement) {
        Object status = value.get("status");
        Object headers = value.get("ordered_headers");
        Object body = decodeBytes(value.get("body_bytes"));
        boolean headersTuple = true;
        if (replacement != null) {
            if (replacement.containsKey("status")) status = decodeValue(replacement.get("status"));
            if (replacement.containsKey("ordered_headers")) {
                headers = decodeValue(replacement.get("ordered_headers"));
                headersTuple = false;
            }
            if (replacement.containsKey("body_bytes")) body = decodeValue(replacement.get("body_bytes"));
        }
        return new RawResponse(status, headers, headersTuple, body);
    }

    private static List<Header> headersFromCorpus(Object value) {
        List<Header> result = new ArrayList<Header>();
        for (Object item : asList(value)) {
            List<Object> pair = asList(item);
            if (pair.size() != 2) throw new CorpusProblem("CORPUS_INVALID");
            result.add(new Header(asString(pair.get(0)), asString(pair.get(1))));
        }
        return result;
    }

    private static byte[] decodeBytes(Object descriptor) {
        if (descriptor == null) return null;
        Map<String, Object> value = asMap(descriptor);
        if (!exactKeys(value, "encoding", "data")) throw new CorpusProblem("CORPUS_INVALID");
        String encoding = asString(value.get("encoding"));
        String data = asString(value.get("data"));
        if ("utf8".equals(encoding)) return data.getBytes(StandardCharsets.UTF_8);
        if ("hex".equals(encoding)) {
            if ((data.length() & 1) != 0) throw new CorpusProblem("CORPUS_INVALID");
            byte[] result = new byte[data.length() / 2];
            for (int index = 0; index < data.length(); index += 2) {
                int high = Character.digit(data.charAt(index), 16);
                int low = Character.digit(data.charAt(index + 1), 16);
                if (high < 0 || low < 0) throw new CorpusProblem("CORPUS_INVALID");
                result[index / 2] = (byte) ((high << 4) | low);
            }
            return result;
        }
        throw new CorpusProblem("CORPUS_INVALID");
    }

    private static Object timeoutFromCorpus(Object value) {
        if (!(value instanceof Map)) return decodeValue(value);
        Map<String, Object> encoded = asMap(value);
        if (!exactKeys(encoded, "literal_encoding")) throw new CorpusProblem("CORPUS_INVALID");
        Map<String, Object> wrapper = new LinkedHashMap<String, Object>();
        wrapper.put("$corpus_value", encoded.get("literal_encoding"));
        return decodeValue(wrapper);
    }

    static final class Header {
        final String name;
        final String value;
        Header(String name, String value) { this.name = name; this.value = value; }
        @Override public boolean equals(Object other) {
            return other instanceof Header && name.equals(((Header) other).name) && value.equals(((Header) other).value);
        }

        @Override public int hashCode() { return 31 * name.hashCode() + value.hashCode(); }
    }

    static final class AbstractRequest {
        final String method;
        final String absoluteUrl;
        final List<Header> orderedHeaders;
        final byte[] bodyBytes;

        AbstractRequest(String method, String absoluteUrl, List<Header> orderedHeaders, byte[] bodyBytes) {
            this.method = method;
            this.absoluteUrl = absoluteUrl;
            this.orderedHeaders = Collections.unmodifiableList(new ArrayList<Header>(orderedHeaders));
            this.bodyBytes = bodyBytes == null ? null : bodyBytes.clone();
        }

        @Override public boolean equals(Object other) {
            if (!(other instanceof AbstractRequest)) return false;
            AbstractRequest that = (AbstractRequest) other;
            return method.equals(that.method) && absoluteUrl.equals(that.absoluteUrl)
                    && orderedHeaders.equals(that.orderedHeaders) && Arrays.equals(bodyBytes, that.bodyBytes);
        }

        @Override public int hashCode() { return method.hashCode(); }
    }

    static final class RawResponse {
        final Object status;
        final Object orderedHeaders;
        final boolean orderedHeadersTuple;
        final Object bodyBytes;

        RawResponse(Object status, Object orderedHeaders, boolean orderedHeadersTuple, Object bodyBytes) {
            this.status = status;
            this.orderedHeaders = orderedHeaders;
            this.orderedHeadersTuple = orderedHeadersTuple;
            this.bodyBytes = bodyBytes;
        }

        @Override public boolean equals(Object other) {
            if (!(other instanceof RawResponse)) return false;
            RawResponse that = (RawResponse) other;
            return jsonEqual(status, that.status) && jsonEqual(orderedHeaders, that.orderedHeaders)
                    && orderedHeadersTuple == that.orderedHeadersTuple
                    && bodyEquals(bodyBytes, that.bodyBytes);
        }

        @Override public int hashCode() {
            int result = status == null ? 0 : status.hashCode();
            result = 31 * result + (orderedHeaders == null ? 0 : orderedHeaders.hashCode());
            result = 31 * result + Boolean.valueOf(orderedHeadersTuple).hashCode();
            result = 31 * result + (bodyBytes instanceof byte[]
                    ? Arrays.hashCode((byte[]) bodyBytes) : bodyBytes == null ? 0 : bodyBytes.hashCode());
            return result;
        }

        private static boolean bodyEquals(Object left, Object right) {
            if (left instanceof byte[] && right instanceof byte[]) return Arrays.equals((byte[]) left, (byte[]) right);
            return jsonEqual(left, right);
        }
    }

    static final class ValueState {
        final boolean missing;
        final Object value;
        ValueState(boolean missing, Object value) { this.missing = missing; this.value = value; }
        static ValueState missing() { return new ValueState(true, null); }
        static ValueState value(Object value) { return new ValueState(false, value); }
    }

    static final class ExecutionError extends Exception {
        private static final long serialVersionUID = 1L;
        final String code;
        final String path;
        final String detail;

        ExecutionError(String code, String path, String detail) {
            this.code = code;
            this.path = path;
            this.detail = detail;
        }

        boolean matches(Map<String, Object> expected) {
            return code.equals(expected.get("code"))
                    && path.equals(expected.get("path"))
                    && detail.equals(expected.get("message"));
        }
    }

    interface CorpusTransport {
        RawResponse sendOnce(AbstractRequest request, Object timeout) throws Exception;
    }

    static final class FakeTransport implements CorpusTransport {
        final Map<String, Object> transport;
        int calls;

        FakeTransport(Map<String, Object> transport) { this.transport = transport; }

        public RawResponse sendOnce(AbstractRequest request, Object timeout) throws Exception {
            calls++;
            String outcome = asString(transport.get("outcome"));
            if ("response".equals(outcome)) {
                Map<String, Object> replacement = null;
                if (transport.containsKey("raw_response_encoding")) {
                    replacement = asMap(asMap(transport.get("raw_response_encoding")).get("fields"));
                }
                return rawResponseFromCorpus(asMap(transport.get("response")), replacement);
            }
            if ("invalid_response".equals(outcome)) return null;
            if ("error".equals(outcome) || "injected_execution_error".equals(outcome)) {
                throw new IOException("synthetic");
            }
            throw new CorpusProblem("CORPUS_INVALID");
        }
    }

    static final class ProviderRegistry {
        final Map<String, Map<String, Object>> rows = new HashMap<String, Map<String, Object>>();
        final List<Object> calls = new ArrayList<Object>();

        ProviderRegistry(List<Object> source) {
            for (Object item : source) {
                Map<String, Object> row = asMap(item);
                String key = providerKey(asString(row.get("kind")), asString(row.get("key")));
                if (rows.put(key, row) != null) throw new CorpusProblem("CORPUS_INVALID");
            }
        }

        Object resolve(Map<String, Object> source, String path, Map<String, Object> memo) throws ExecutionError {
            String kind = source.get("kind") instanceof String ? (String) source.get("kind") : null;
            Object rawIdentifier = "secret_handle".equals(kind) ? source.get("handle") : source.get("name");
            if (!(rawIdentifier instanceof String) || ((String) rawIdentifier).isEmpty()) {
                throw error("INVALID_PROVIDER_SOURCE", path, "Provider source is invalid.");
            }
            String identifier = (String) rawIdentifier;
            String key = providerKey(kind, identifier);
            if (memo.containsKey(key)) return memo.get(key);
            Map<String, Object> row = rows.get(key);
            if (row == null) {
                throw error("PREFLIGHT_PROVIDER_MISSING", source.containsKey("name") ? path + "/name" : path,
                        "Required provider value is missing.");
            }
            Map<String, Object> call = new LinkedHashMap<String, Object>();
            call.put("kind", kind);
            call.put("key", identifier);
            calls.add(call);
            String state = asString(row.get("state"));
            if ("MISSING".equals(state)) {
                throw error("PREFLIGHT_PROVIDER_MISSING", source.containsKey("name") ? path + "/name" : path,
                        "Required provider value is missing.");
            }
            if ("ERROR".equals(state) || "DENIED".equals(state)) {
                throw error("PREFLIGHT_PROVIDER_ERROR", path, "Required provider could not be resolved.");
            }
            if (!"VALUE".equals(state)) throw new CorpusProblem("CORPUS_INVALID");
            Object value = decodeValue(row.get("value"));
            memo.put(key, value);
            return value;
        }
    }

    static final class StepOutputs {
        final Map<String, Object> values = new HashMap<String, Object>();
        StepOutputs(List<Object> source) {
            for (Object item : source) {
                Map<String, Object> row = asMap(item);
                String key = outputKey(asString(row.get("step_id")), asString(row.get("output_id")));
                if (values.containsKey(key)) throw new CorpusProblem("CORPUS_INVALID");
                String state = asString(row.get("state"));
                if ("VALUE".equals(state)) values.put(key, decodeValue(row.get("value")));
                else if ("MISSING".equals(state)) values.put(key, Missing.INSTANCE);
                else throw new CorpusProblem("CORPUS_INVALID");
            }
        }
    }

    enum Missing { INSTANCE }

    private static String providerKey(String kind, String identifier) { return kind + "\u0000" + identifier; }
    private static String outputKey(String step, String output) { return step + "\u0000" + output; }

    private static ExecutionError error(String code, String path, String detail) {
        return new ExecutionError(code, path, detail);
    }

    static final class HttpSemantics {
        private HttpSemantics() { }

        static AbstractRequest buildRequest(Object operationValue, Object inputsValue,
                                            ProviderRegistry providers, StepOutputs outputs)
                throws ExecutionError {
            if (!(operationValue instanceof Map)) {
                throw error("INVALID_OPERATION_SHAPE", "/operation", "Operation must be the exact canonical HTTP object.");
            }
            if (!(inputsValue instanceof List)) {
                throw error("INVALID_INPUT", "/inputs", "inputs must be an ordered array.");
            }
            Map<String, Object> operation = asMap(operationValue);
            List<Object> inputs = asList(inputsValue);
            validateStaticContract(operation, inputs);
            validateStaticLiterals(inputs);

            Map<String, Object> memo = new HashMap<String, Object>();
            Map<String, Object> baseSource = asMap(operation.get("base_url_source"));
            Object baseValue = providers.resolve(baseSource, "/base_url_source", memo);
            if (!(baseValue instanceof String) || ((String) baseValue).isEmpty()) {
                throw error("INVALID_BASE_URL", "/base_url_source/name", "Base URL must be a non-empty ASCII origin.");
            }
            String base = (String) baseValue;
            validateBaseUrl(base);

            Map<Integer, Object> externalValues = new HashMap<Integer, Object>();
            for (int index = 0; index < inputs.size(); index++) {
                Map<String, Object> input = asMap(inputs.get(index));
                Map<String, Object> source = asMap(input.get("source"));
                if (isExternal(source)) {
                    Object value = providers.resolve(source, "/inputs/" + index + "/source", memo);
                    validateSourceValue(value, input, index, true);
                    externalValues.put(Integer.valueOf(index), value);
                }
            }

            Map<String, BoundPath> pathValues = new HashMap<String, BoundPath>();
            List<QueryRow> queryRows = new ArrayList<QueryRow>();
            List<Header> headers = new ArrayList<Header>();
            List<BodyRow> bodyRows = new ArrayList<BodyRow>();
            Set<String> seenTargets = new HashSet<String>();
            for (int index = 0; index < inputs.size(); index++) {
                Map<String, Object> input = asMap(inputs.get(index));
                Map<String, Object> target = asMap(input.get("target"));
                Map<String, Object> source = asMap(input.get("source"));
                String itemPath = "/inputs/" + index;
                Object value = externalValues.containsKey(Integer.valueOf(index))
                        ? externalValues.get(Integer.valueOf(index))
                        : resolveSource(source, providers, outputs, itemPath, memo);
                if (!externalValues.containsKey(Integer.valueOf(index))) validateSourceValue(value, input, index, false);
                String location = asString(target.get("location"));
                if ("path".equals(location)) {
                    String name = targetName(target, itemPath);
                    distinctTarget(seenTargets, location, name, itemPath);
                    if (!(value instanceof String)) {
                        throw error("RUNTIME_TYPE_MISMATCH", itemPath + "/source", "path target requires a string value.");
                    }
                    pathValues.put(name, new BoundPath((String) value, itemPath));
                } else if ("query".equals(location)) {
                    String name = targetName(target, itemPath);
                    distinctTarget(seenTargets, location, name, itemPath);
                    if (!isScalar(value)) {
                        throw error("RUNTIME_TYPE_MISMATCH", itemPath + "/source", "query target requires a JSON scalar value.");
                    }
                    queryRows.add(new QueryRow(name, value));
                } else if ("header".equals(location)) {
                    String name = targetName(target, itemPath);
                    distinctTarget(seenTargets, location, name, itemPath);
                    validateHeaderName(name, itemPath + "/target/name");
                    if (!(value instanceof String)) {
                        throw error("RUNTIME_TYPE_MISMATCH", itemPath + "/source", "header target requires a string value.");
                    }
                    validateHeaderValue((String) value, itemPath + "/source");
                    headers.add(new Header(name, (String) value));
                } else {
                    List<String> tokens = pointerTokens(target.get("pointer"), itemPath + "/target/pointer");
                    bodyRows.add(new BodyRow(tokens, value, itemPath));
                }
            }

            Set<String> namesInPath = placeholderNames(asString(operation.get("path")));
            if (!namesInPath.equals(pathValues.keySet())) {
                throw error("INVALID_PATH_BINDINGS", "/path", "Path placeholders must exactly match path input names.");
            }
            String renderedPath = renderPath(asString(operation.get("path")), pathValues);
            byte[] body = buildBody(bodyRows);
            headers.add(new Header("accept-encoding", "identity"));
            headers.add(new Header("user-agent", "test-skills-http-binding-v1"));
            if (body != null) headers.add(new Header("content-type", "application/json"));
            StringBuilder query = new StringBuilder();
            for (QueryRow row : queryRows) {
                if (query.length() > 0) query.append('&');
                query.append(percentEncode(row.name)).append('=').append(percentEncode(queryLexeme(row.value)));
            }
            return new AbstractRequest(asString(operation.get("method")),
                    base + renderedPath + (query.length() == 0 ? "" : "?" + query), headers, body);
        }

        static RawResponse executeOnce(AbstractRequest request, CorpusTransport transport, Object timeout)
                throws ExecutionError {
            if (request == null) throw error("INVALID_REQUEST", "/request", "request must be an AbstractRequest.");
            if (!validTimeout(timeout)) {
                throw error("INVALID_TIMEOUT", "/timeout_seconds", "timeout_seconds must be a finite positive number.");
            }
            RawResponse response;
            try {
                response = transport.sendOnce(request, timeout);
            } catch (Exception failure) {
                throw error("TRANSPORT_ERROR", "/transport", "Transport did not produce a response.");
            }
            if (response == null) {
                throw error("INVALID_TRANSPORT_RESPONSE", "/transport", "Transport must return RawResponse.");
            }
            return response;
        }

        static ValueState observeResponse(RawResponse response, Object sourceValue) throws ExecutionError {
            if (response == null) throw error("INVALID_RESPONSE", "/response", "response must be RawResponse.");
            if (!(response.status instanceof JsonNumber) || !((JsonNumber) response.status).integer) {
                throw error("INVALID_RESPONSE", "/status", "Response status must be an integer.");
            }
            if (!(sourceValue instanceof Map)) {
                throw error("INVALID_RESPONSE_SOURCE", "/source", "Response source must be an object.");
            }
            Map<String, Object> source = asMap(sourceValue);
            Object rawKind = source.get("kind");
            String kind = rawKind instanceof String ? (String) rawKind : null;
            boolean exact = "http_status".equals(kind) ? exactKeys(source, "kind")
                    : "http_header".equals(kind) ? exactKeys(source, "kind", "name")
                    : "http_body".equals(kind) && exactKeys(source, "kind", "pointer");
            if (!exact) {
                throw error("INVALID_RESPONSE_SOURCE", "/source", "Response source must be an exact canonical object.");
            }
            if ("http_status".equals(kind)) return ValueState.value(response.status);
            if ("http_header".equals(kind)) {
                Object rawName = source.get("name");
                if (!(rawName instanceof String) || !HEADER_NAME.matcher((String) rawName).matches()) {
                    throw error("INVALID_RESPONSE_SOURCE", "/source/name",
                            "Header source name must be canonical lowercase RFC 9110 tchar.");
                }
                String name = (String) rawName;
                List<String> fields = headerFields(response, name);
                if (fields.isEmpty()) return ValueState.missing();
                if (fields.size() != 1) {
                    throw error("DUPLICATE_RESPONSE_HEADER", "/headers/" + name.toLowerCase(Locale.ROOT),
                            "Response contains duplicate header fields.");
                }
                return ValueState.value(stripOptionalWhitespace(fields.get(0)));
            }
            List<String> tokens = pointerTokens(source.get("pointer"), "/source/pointer");
            List<String> coding = headerFields(response, "content-encoding");
            if (!coding.isEmpty() && (coding.size() != 1
                    || !"identity".equals(stripOptionalWhitespace(coding.get(0)).toLowerCase(Locale.ROOT)))) {
                throw error("UNSUPPORTED_CONTENT_ENCODING", "/headers/content-encoding",
                        "Response body content encoding must be identity.");
            }
            if (!(response.bodyBytes instanceof byte[])) {
                throw error("INVALID_RESPONSE_JSON", "/body", "Response body must be strict UTF-8 JSON.");
            }
            byte[] raw = (byte[]) response.bodyBytes;
            if (raw.length == 0) return ValueState.missing();
            Object value = strictResponseJson(raw);
            for (String token : tokens) {
                if (value instanceof Map) {
                    Map<String, Object> object = asMap(value);
                    if (!object.containsKey(token)) return ValueState.missing();
                    value = object.get(token);
                } else if (value instanceof List) {
                    if (!token.matches("0|[1-9][0-9]*")) return ValueState.missing();
                    int index;
                    try { index = Integer.parseInt(token); }
                    catch (NumberFormatException failure) { return ValueState.missing(); }
                    List<Object> array = asList(value);
                    if (index >= array.size()) return ValueState.missing();
                    value = array.get(index);
                } else {
                    return ValueState.missing();
                }
            }
            return ValueState.value(value);
        }

        private static void validateStaticContract(Map<String, Object> operation, List<Object> inputs)
                throws ExecutionError {
            if (!exactKeys(operation, "kind", "binding_profile", "base_url_source", "method", "path")) {
                throw error("INVALID_OPERATION_SHAPE", "/operation", "Operation must be the exact canonical HTTP object.");
            }
            requireHttpOperation(operation);
            Object rawBase = operation.get("base_url_source");
            if (!(rawBase instanceof Map)) {
                throw error("INVALID_BASE_URL_SOURCE", "/base_url_source", "base_url_source must be the exact canonical object.");
            }
            Map<String, Object> base = asMap(rawBase);
            if (!exactKeys(base, "kind", "name", "provenance") || !"environment".equals(base.get("kind"))
                    || !identifier(base.get("name")) || !provenance(base.get("provenance"))) {
                throw error("INVALID_BASE_URL_SOURCE", "/base_url_source", "base_url_source must be the exact canonical object.");
            }
            if (!(operation.get("path") instanceof String)) {
                throw error("INVALID_PATH_TEMPLATE", "/path", "HTTP path must be a string.");
            }
            validatePathTemplate((String) operation.get("path"));
            Set<String> pathNames = new HashSet<String>();
            Set<String> seen = new HashSet<String>();
            List<List<String>> bodyPaths = new ArrayList<List<String>>();
            for (int index = 0; index < inputs.size(); index++) {
                String path = "/inputs/" + index;
                Object rawInput = inputs.get(index);
                if (!(rawInput instanceof Map)) {
                    throw error("INVALID_INPUT_SHAPE", path, "Input must be an exact canonical object.");
                }
                Map<String, Object> input = asMap(rawInput);
                boolean shape = exactKeys(input, "input_id", "display_order", "target", "source", "semantic_type")
                        || exactKeys(input, "input_id", "display_order", "target", "source", "semantic_type", "type_provenance");
                if (!shape || !(input.get("input_id") instanceof String)
                        || !INPUT_ID.matcher((String) input.get("input_id")).matches()
                        || !(input.get("display_order") instanceof JsonNumber)
                        || !((JsonNumber) input.get("display_order")).integer
                        || ((JsonNumber) input.get("display_order")).decimal.compareTo(BigDecimal.valueOf(index + 1L)) != 0) {
                    throw error("INVALID_INPUT_SHAPE", path, "Input must be an exact canonical object.");
                }
                validateDescriptor(input.get("semantic_type"), path + "/semantic_type");
                if (!(input.get("target") instanceof Map) || !(input.get("source") instanceof Map)) {
                    throw error("INVALID_INPUT_SHAPE", path, "Input must be an exact canonical object.");
                }
                Map<String, Object> target = asMap(input.get("target"));
                Map<String, Object> source = asMap(input.get("source"));
                if (!target.containsKey("sensitive")) {
                    throw error("INVALID_INPUT_SHAPE", path, "Input must be an exact canonical object.");
                }
                validateTarget(target, path + "/target");
                validateSourceShape(source, path + "/source");
                boolean external = isExternal(source);
                if (external != input.containsKey("type_provenance")
                        || external && !provenance(input.get("type_provenance"))) {
                    throw error("INVALID_INPUT_SHAPE", path, "type_provenance has the wrong conditional presence.");
                }
                if (!Boolean.valueOf("secret_handle".equals(source.get("kind"))).equals(target.get("sensitive"))) {
                    throw error("INVALID_TARGET", path + "/target/sensitive", "sensitive must match source kind.");
                }
                String location = asString(target.get("location"));
                if ("arg".equals(location)) {
                    throw error("INVALID_TARGET", path + "/target/location", "HTTP input cannot target a capability argument.");
                }
                if ("path".equals(location) || "query".equals(location) || "header".equals(location)) {
                    String name = asString(target.get("name"));
                    String key = location + "\u0000" + name;
                    if (!seen.add(key)) throw error("DUPLICATE_TARGET", path, "Input target is duplicated.");
                    if ("path".equals(location)) pathNames.add(name);
                    if ("header".equals(location)) validateHeaderName(name, path + "/target/name");
                } else {
                    List<String> tokens = pointerTokens(target.get("pointer"), path + "/target/pointer");
                    for (List<String> prior : bodyPaths) {
                        if (overlap(tokens, prior)) {
                            throw error("OVERLAPPING_BODY_POINTER", path + "/target/pointer", "Body pointers may not overlap.");
                        }
                    }
                    bodyPaths.add(tokens);
                }
            }
            if (!placeholderNames((String) operation.get("path")).equals(pathNames)) {
                throw error("INVALID_PATH_BINDINGS", "/path", "Path placeholders must exactly match path input names.");
            }
        }

        private static void requireHttpOperation(Map<String, Object> operation) throws ExecutionError {
            if (!"http".equals(operation.get("kind")) || !"http-binding-v1".equals(operation.get("binding_profile"))) {
                throw error("INVALID_HTTP_PROFILE", "/operation", "Operation must use http-binding-v1.");
            }
            Object method = operation.get("method");
            if (!(method instanceof String) || !METHOD.matcher((String) method).matches()) {
                throw error("INVALID_METHOD", "/method", "HTTP method is invalid.");
            }
        }

        private static void validateStaticLiterals(List<Object> inputs) throws ExecutionError {
            for (int index = 0; index < inputs.size(); index++) {
                Map<String, Object> input = asMap(inputs.get(index));
                Map<String, Object> source = asMap(input.get("source"));
                if (!"literal".equals(source.get("kind"))) continue;
                Object value = source.get("value");
                validateJsonValue(value, "/inputs/" + index + "/source/value");
                String location = asString(asMap(input.get("target")).get("location"));
                if ("path".equals(location) && (".".equals(value) || "..".equals(value))) {
                    throw error("INVALID_PATH_VALUE", "/inputs/" + index, "Rendered path must not contain dot segments.");
                }
                if ("header".equals(location)) {
                    if (!(value instanceof String)) {
                        throw error("RUNTIME_TYPE_MISMATCH", "/inputs/" + index + "/source", "header target requires a string value.");
                    }
                    validateHeaderValue((String) value, "/inputs/" + index + "/source");
                }
                validateSourceValue(value, input, index, true);
            }
        }

        private static void validateTarget(Map<String, Object> target, String path) throws ExecutionError {
            if (!(target.get("location") instanceof String)) {
                throw error("INVALID_TARGET", path, "Target must be a canonical object.");
            }
            String location = (String) target.get("location");
            boolean exact = "body".equals(location)
                    ? exactKeys(target, "location", "pointer", "sensitive")
                    : ("path".equals(location) || "query".equals(location) || "header".equals(location) || "arg".equals(location))
                    && exactKeys(target, "location", "name", "sensitive");
            if (!exact || !(target.get("sensitive") instanceof Boolean)) {
                throw error("INVALID_TARGET", path, "Target must be an exact canonical object.");
            }
            if ("body".equals(location)) return;
            if ("header".equals(location)) validateHeaderNameObject(target.get("name"), path + "/name");
            boolean valid = "arg".equals(location) ? identifier(target.get("name")) : nonempty(target.get("name"));
            if (!valid) throw error("INVALID_TARGET", path + "/name", "Target name is invalid.");
        }

        private static void validateSourceShape(Map<String, Object> source, String path) throws ExecutionError {
            if (!(source.get("kind") instanceof String)) {
                throw error("INVALID_SOURCE", path, "Source must be a canonical object.");
            }
            String kind = (String) source.get("kind");
            boolean exact = "literal".equals(kind) ? exactKeys(source, "kind", "value")
                    : "step_output".equals(kind) ? exactKeys(source, "kind", "step_id", "output_id")
                    : ("fixture".equals(kind) || "environment".equals(kind)) ? exactKeys(source, "kind", "name")
                    : "secret_handle".equals(kind) && exactKeys(source, "kind", "handle", "safe_label");
            if (!exact) throw error("INVALID_SOURCE", path, "Source must be an exact canonical object.");
            if ("step_output".equals(kind)
                    && (!(source.get("step_id") instanceof String) || !STEP_ID.matcher((String) source.get("step_id")).matches()
                    || !identifier(source.get("output_id")))) {
                throw error("INVALID_SOURCE", path, "step_output identifiers are invalid.");
            }
            if (("fixture".equals(kind) || "environment".equals(kind)) && !nonempty(source.get("name"))) {
                throw error("INVALID_SOURCE", path, "Provider name is invalid.");
            }
            if ("secret_handle".equals(kind) && (!nonempty(source.get("handle")) || !nonempty(source.get("safe_label")))) {
                throw error("INVALID_SOURCE", path, "Secret provider fields are invalid.");
            }
        }

        private static void validateDescriptor(Object value, String path) throws ExecutionError {
            if (!(value instanceof Map)) {
                throw error("INVALID_SEMANTIC_TYPE", path, "semantic_type must be an exact descriptor.");
            }
            Map<String, Object> descriptor = asMap(value);
            Set<String> representations = setOf("null", "boolean", "integer", "number", "string", "array", "object");
            boolean valid = exactKeys(descriptor, "kind", "type") && "json".equals(descriptor.get("kind"))
                    && representations.contains(descriptor.get("type"))
                    || exactKeys(descriptor, "kind", "name", "representation") && "named".equals(descriptor.get("kind"))
                    && identifier(descriptor.get("name")) && representations.contains(descriptor.get("representation"));
            if (!valid) throw error("INVALID_SEMANTIC_TYPE", path, "semantic_type must be an exact descriptor.");
        }

        private static void validateSourceValue(Object value, Map<String, Object> input, int index, boolean preflight)
                throws ExecutionError {
            String path = "/inputs/" + index + "/source";
            validateJsonValue(value, path);
            Map<String, Object> descriptor = asMap(input.get("semantic_type"));
            String expected = descriptor.containsKey("type") ? asString(descriptor.get("type")) : asString(descriptor.get("representation"));
            String actual = representation(value);
            Map<String, Object> source = asMap(input.get("source"));
            if (!actual.equals(expected) && !("integer".equals(actual) && "number".equals(expected))) {
                String code = preflight && !"literal".equals(source.get("kind"))
                        ? "PREFLIGHT_PROVIDER_TYPE_MISMATCH" : "RUNTIME_TYPE_MISMATCH";
                throw error(code, path, "Resolved value does not match semantic_type.");
            }
            Map<String, Object> target = asMap(input.get("target"));
            String targetCode = preflight && !"literal".equals(source.get("kind")) ? "PREFLIGHT_PROVIDER_TARGET_ERROR"
                    : !preflight && "step_output".equals(source.get("kind")) ? "RUNTIME_STEP_OUTPUT_TARGET_ERROR"
                    : "RUNTIME_TYPE_MISMATCH";
            String targetMessage = "PREFLIGHT_PROVIDER_TARGET_ERROR".equals(targetCode)
                    ? "Resolved provider value is invalid for its HTTP target."
                    : "RUNTIME_STEP_OUTPUT_TARGET_ERROR".equals(targetCode)
                    ? "Resolved step output is invalid for its HTTP target." : null;
            String location = asString(target.get("location"));
            if ("path".equals(location)) {
                if (!(value instanceof String) || ".".equals(value) || "..".equals(value)) {
                    throw error(targetCode, path, targetMessage != null ? targetMessage
                            : !(value instanceof String) ? "path target requires a string value."
                            : "Rendered path must not contain dot segments.");
                }
            } else if ("query".equals(location) && !isScalar(value)) {
                throw error(targetCode, path, targetMessage != null ? targetMessage : "query target requires a JSON scalar value.");
            } else if ("header".equals(location)) {
                if (!(value instanceof String)) {
                    throw error(targetCode, path, targetMessage != null ? targetMessage : "header target requires a string value.");
                }
                try {
                    validateHeaderValue((String) value, path);
                } catch (ExecutionError invalid) {
                    throw error(targetCode, path, targetMessage != null ? targetMessage
                            : "Header value must be ASCII without controls or outer space.");
                }
            }
        }

        private static Object resolveSource(Map<String, Object> source, ProviderRegistry providers,
                                            StepOutputs outputs, String itemPath, Map<String, Object> memo)
                throws ExecutionError {
            String kind = asString(source.get("kind"));
            if ("literal".equals(kind)) return source.get("value");
            if ("step_output".equals(kind)) {
                String key = outputKey(asString(source.get("step_id")), asString(source.get("output_id")));
                if (!outputs.values.containsKey(key) || outputs.values.get(key) == Missing.INSTANCE) {
                    throw error("RUNTIME_STEP_OUTPUT_MISSING", itemPath + "/source", "Referenced step output is missing.");
                }
                return outputs.values.get(key);
            }
            if (isExternal(source)) return providers.resolve(source, itemPath + "/source", memo);
            throw error("INVALID_SOURCE", itemPath + "/source/kind", "Input source kind is invalid.");
        }

        private static boolean isExternal(Map<String, Object> source) {
            return "fixture".equals(source.get("kind")) || "environment".equals(source.get("kind"))
                    || "secret_handle".equals(source.get("kind"));
        }

        private static boolean identifier(Object value) {
            return value instanceof String && IDENTIFIER.matcher((String) value).matches();
        }

        private static boolean nonempty(Object value) {
            return value instanceof String && !((String) value).trim().isEmpty();
        }

        private static boolean provenance(Object value) {
            if (!(value instanceof List) || ((List<?>) value).isEmpty()) return false;
            for (Object item : (List<?>) value) if (!nonempty(item)) return false;
            return true;
        }

        private static String representation(Object value) {
            if (value == null) return "null";
            if (value instanceof Boolean) return "boolean";
            if (value instanceof JsonNumber) return ((JsonNumber) value).integer ? "integer" : "number";
            if (value instanceof String) return "string";
            if (value instanceof List) return "array";
            return "object";
        }

        private static boolean isScalar(Object value) {
            return value == null || value instanceof String || value instanceof Boolean || value instanceof JsonNumber;
        }

        private static boolean validTimeout(Object value) {
            return value instanceof JsonNumber && ((JsonNumber) value).decimal.signum() > 0
                    || value instanceof Double && Double.isFinite(((Double) value).doubleValue()) && ((Double) value).doubleValue() > 0;
        }

        private static void validateBaseUrl(String value) throws ExecutionError {
            if (!isAscii(value) || !value.matches("https?://[^/?#\\s]+")) {
                throw error("INVALID_BASE_URL", "/base_url_source/name", "Base URL must be an exact ASCII origin.");
            }
            int delimiter = value.indexOf("://");
            String scheme = value.substring(0, delimiter);
            String authority = value.substring(delimiter + 3);
            if (!("http".equals(scheme) || "https".equals(scheme)) || authority.indexOf('@') >= 0
                    || count(authority, ':') > 1) {
                throw error("INVALID_BASE_URL", "/base_url_source/name", "Base URL must be an exact ASCII origin.");
            }
            int colon = authority.indexOf(':');
            String host = colon < 0 ? authority : authority.substring(0, colon);
            String port = colon < 0 ? null : authority.substring(colon + 1);
            if (host.isEmpty() || port != null && (!port.matches("[1-9][0-9]{0,4}") || integer(port) < 1 || integer(port) > 65535)) {
                throw error("INVALID_BASE_URL", "/base_url_source/name", "Base URL host or port is invalid.");
            }
            String[] labels = host.split("\\.", -1);
            boolean ipv4Shape = labels.length == 4;
            for (String label : labels) ipv4Shape &= label.matches("[0-9]+");
            if (ipv4Shape) {
                for (String label : labels) {
                    if (!label.matches("0|[1-9][0-9]{0,2}") || integer(label) > 255) {
                        throw error("INVALID_BASE_URL", "/base_url_source/name", "Base URL host or port is invalid.");
                    }
                }
                return;
            }
            if (host.length() > 253) {
                throw error("INVALID_BASE_URL", "/base_url_source/name", "Base URL DNS host is invalid.");
            }
            for (String label : labels) {
                if (!DNS_LABEL.matcher(label).matches()) {
                    throw error("INVALID_BASE_URL", "/base_url_source/name", "Base URL DNS host is invalid.");
                }
            }
        }

        private static int integer(String value) {
            try { return Integer.parseInt(value); }
            catch (NumberFormatException error) { return Integer.MAX_VALUE; }
        }

        private static int count(String value, char character) {
            int result = 0;
            for (int index = 0; index < value.length(); index++) if (value.charAt(index) == character) result++;
            return result;
        }

        private static void validatePathTemplate(String path) throws ExecutionError {
            if (!path.startsWith("/") || !isAscii(path) || path.indexOf('?') >= 0 || path.indexOf('#') >= 0
                    || path.indexOf('\\') >= 0) {
                throw error("INVALID_PATH_TEMPLATE", "/path", "Path template is invalid.");
            }
            for (int index = 0; index < path.length(); index++) {
                int code = path.charAt(index);
                if (code < 0x21 || code == 0x7f) {
                    throw error("INVALID_PATH_TEMPLATE", "/path", "Path template is invalid.");
                }
            }
            Matcher matcher = PLACEHOLDER.matcher(path);
            String withoutPlaceholders = matcher.replaceAll("");
            if (withoutPlaceholders.indexOf('{') >= 0 || withoutPlaceholders.indexOf('}') >= 0) {
                throw error("INVALID_PATH_TEMPLATE", "/path", "Path placeholder is invalid.");
            }
            matcher.reset();
            int cursor = 0;
            while (matcher.find()) {
                validatePathLiteral(path.substring(cursor, matcher.start()));
                cursor = matcher.end();
            }
            validatePathLiteral(path.substring(cursor));
            for (String segment : path.split("/", -1)) {
                if (segment.indexOf('{') < 0 && (".".equals(segment) || "..".equals(segment))) {
                    throw error("INVALID_PATH_TEMPLATE", "/path", "Literal dot path segments are forbidden.");
                }
            }
        }

        private static void validatePathLiteral(String literal) throws ExecutionError {
            String allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=:@/%";
            for (int index = 0; index < literal.length(); index++) {
                char character = literal.charAt(index);
                if (allowed.indexOf(character) < 0) {
                    throw error("INVALID_PATH_TEMPLATE", "/path", "Path contains an invalid literal character.");
                }
                if (character == '%') {
                    if (index + 2 >= literal.length() || !upperHex(literal.charAt(index + 1)) || !upperHex(literal.charAt(index + 2))) {
                        throw error("INVALID_PATH_TEMPLATE", "/path", "Path contains an invalid literal character.");
                    }
                    index += 2;
                }
            }
        }

        private static boolean upperHex(char value) {
            return value >= '0' && value <= '9' || value >= 'A' && value <= 'F';
        }

        private static Set<String> placeholderNames(String path) {
            Set<String> result = new HashSet<String>();
            Matcher matcher = PLACEHOLDER.matcher(path);
            while (matcher.find()) result.add(matcher.group(1));
            return result;
        }

        private static String renderPath(String template, Map<String, BoundPath> values) throws ExecutionError {
            Matcher matcher = PLACEHOLDER.matcher(template);
            StringBuilder result = new StringBuilder();
            int cursor = 0;
            while (matcher.find()) {
                result.append(template, cursor, matcher.start());
                result.append(percentEncode(values.get(matcher.group(1)).value));
                cursor = matcher.end();
            }
            result.append(template.substring(cursor));
            for (String segment : result.toString().split("/", -1)) {
                if (".".equals(segment) || "..".equals(segment)) {
                    for (String name : values.keySet()) {
                        BoundPath value = values.get(name);
                        if (".".equals(value.value) || "..".equals(value.value)) {
                            throw error("INVALID_PATH_VALUE", value.owner, "Rendered path must not contain dot segments.");
                        }
                    }
                    throw error("INVALID_PATH_VALUE", "/path", "Rendered path must not contain dot segments.");
                }
            }
            return result.toString();
        }

        private static String targetName(Map<String, Object> target, String itemPath) throws ExecutionError {
            Object value = target.get("name");
            if (!(value instanceof String) || ((String) value).isEmpty()) {
                throw error("INVALID_TARGET", itemPath + "/target/name", "Target requires a non-empty name.");
            }
            return (String) value;
        }

        private static void distinctTarget(Set<String> seen, String location, String name, String itemPath)
                throws ExecutionError {
            if (!seen.add(location + "\u0000" + name)) {
                throw error("DUPLICATE_TARGET", itemPath, "Input target is duplicated.");
            }
        }

        private static void validateHeaderNameObject(Object name, String path) throws ExecutionError {
            if (!(name instanceof String)) {
                throw error("INVALID_HEADER_NAME", path, "Header name must be lowercase, non-reserved RFC 9110 tchar.");
            }
            validateHeaderName((String) name, path);
        }

        private static void validateHeaderName(String name, String path) throws ExecutionError {
            if (!HEADER_NAME.matcher(name).matches() || RESERVED_HEADERS.contains(name)) {
                throw error("INVALID_HEADER_NAME", path, "Header name must be lowercase, non-reserved RFC 9110 tchar.");
            }
        }

        private static void validateHeaderValue(String value, String path) throws ExecutionError {
            if (!isAscii(value) || !value.equals(stripSpaces(value))) {
                throw error("INVALID_HEADER_VALUE", path, "Header value must be ASCII without controls or outer space.");
            }
            for (int index = 0; index < value.length(); index++) {
                int code = value.charAt(index);
                if (code < 0x20 || code == 0x7f) {
                    throw error("INVALID_HEADER_VALUE", path, "Header value must be ASCII without controls or outer space.");
                }
            }
        }

        private static String stripSpaces(String value) {
            int start = 0;
            int end = value.length();
            while (start < end && value.charAt(start) == ' ') start++;
            while (end > start && value.charAt(end - 1) == ' ') end--;
            return value.substring(start, end);
        }

        private static String stripOptionalWhitespace(String value) {
            int start = 0;
            int end = value.length();
            while (start < end && (value.charAt(start) == ' ' || value.charAt(start) == '\t')) start++;
            while (end > start && (value.charAt(end - 1) == ' ' || value.charAt(end - 1) == '\t')) end--;
            return value.substring(start, end);
        }

        private static List<String> pointerTokens(Object pointer, String path) throws ExecutionError {
            if (!(pointer instanceof String) || !((String) pointer).isEmpty() && !((String) pointer).startsWith("/")) {
                throw error("INVALID_JSON_POINTER", path, "JSON Pointer must be root or start with '/'.");
            }
            String text = (String) pointer;
            if (text.isEmpty()) return new ArrayList<String>();
            List<String> result = new ArrayList<String>();
            for (String raw : text.substring(1).split("/", -1)) {
                StringBuilder token = new StringBuilder();
                for (int index = 0; index < raw.length(); index++) {
                    char character = raw.charAt(index);
                    if (character == '~') {
                        if (index + 1 >= raw.length() || raw.charAt(index + 1) != '0' && raw.charAt(index + 1) != '1') {
                            throw error("INVALID_JSON_POINTER", path, "JSON Pointer has an invalid escape.");
                        }
                        token.append(raw.charAt(++index) == '0' ? '~' : '/');
                    } else {
                        token.append(character);
                    }
                }
                result.add(token.toString());
            }
            return result;
        }

        private static boolean overlap(List<String> left, List<String> right) {
            int minimum = Math.min(left.size(), right.size());
            for (int index = 0; index < minimum; index++) if (!left.get(index).equals(right.get(index))) return false;
            return true;
        }

        private static byte[] buildBody(List<BodyRow> rows) throws ExecutionError {
            if (rows.isEmpty()) return null;
            BodyRow root = null;
            for (BodyRow row : rows) if (row.tokens.isEmpty()) root = row;
            if (root != null && rows.size() != 1) {
                throw error("OVERLAPPING_BODY_POINTER", root.owner, "Root body pointer cannot overlap another body pointer.");
            }
            Object value;
            if (root != null) {
                value = root.value;
            } else {
                Map<String, Object> object = new LinkedHashMap<String, Object>();
                List<List<String>> previous = new ArrayList<List<String>>();
                for (BodyRow row : rows) {
                    for (List<String> prior : previous) {
                        if (overlap(row.tokens, prior)) {
                            throw error("OVERLAPPING_BODY_POINTER", row.owner, "Body pointers may not overlap.");
                        }
                    }
                    previous.add(row.tokens);
                    Map<String, Object> current = object;
                    for (int index = 0; index < row.tokens.size() - 1; index++) {
                        String token = row.tokens.get(index);
                        Object child = current.get(token);
                        if (child == null) {
                            child = new LinkedHashMap<String, Object>();
                            current.put(token, child);
                        }
                        current = asMap(child);
                    }
                    current.put(row.tokens.get(row.tokens.size() - 1), row.value);
                }
                value = object;
            }
            try {
                return MiniJson.canonical(value).getBytes(StandardCharsets.UTF_8);
            } catch (MiniJson.Error failure) {
                throw error("INVALID_BODY_VALUE", "/inputs", "Body value is not canonical JSON.");
            }
        }

        private static String percentEncode(String value) throws ExecutionError {
            validateScalarString(value, "/inputs");
            byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
            StringBuilder result = new StringBuilder();
            String unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
            for (byte raw : bytes) {
                int valueByte = raw & 0xff;
                char character = (char) valueByte;
                if (unreserved.indexOf(character) >= 0) result.append(character);
                else result.append('%').append(String.format(Locale.ROOT, "%02X", Integer.valueOf(valueByte)));
            }
            return result.toString();
        }

        private static String queryLexeme(Object value) throws ExecutionError {
            if (value instanceof String) return (String) value;
            try {
                return MiniJson.canonical(value);
            } catch (MiniJson.Error failure) {
                throw error("INVALID_QUERY_VALUE", "/inputs", "Query value is not a canonical JSON scalar.");
            }
        }

        private static void validateJsonValue(Object value, String path) throws ExecutionError {
            if (value instanceof NonFinite) {
                throw error("INVALID_JSON_VALUE", path, "JSON number must be finite.");
            }
            if (value instanceof String) validateScalarString((String) value, path);
            else if (value instanceof List) {
                List<Object> array = asList(value);
                for (int index = 0; index < array.size(); index++) validateJsonValue(array.get(index), path + "/" + index);
            } else if (value instanceof Map) {
                for (Map.Entry<String, Object> entry : asMap(value).entrySet()) {
                    validateScalarString(entry.getKey(), path);
                    validateJsonValue(entry.getValue(), path + "/" + entry.getKey());
                }
            } else if (value != null && !(value instanceof String) && !(value instanceof Boolean)
                    && !(value instanceof JsonNumber)) {
                throw error("INVALID_JSON_VALUE", path, "Value is not JSON data.");
            }
        }

        private static void validateScalarString(String value, String path) throws ExecutionError {
            for (int index = 0; index < value.length(); index++) {
                char unit = value.charAt(index);
                if (Character.isHighSurrogate(unit)) {
                    if (index + 1 >= value.length() || !Character.isLowSurrogate(value.charAt(index + 1))) {
                        throw error("INVALID_JSON_VALUE", path, "String contains an unpaired surrogate.");
                    }
                    index++;
                } else if (Character.isLowSurrogate(unit)) {
                    throw error("INVALID_JSON_VALUE", path, "String contains an unpaired surrogate.");
                }
            }
        }

        private static List<String> headerFields(RawResponse response, String name) throws ExecutionError {
            if (!response.orderedHeadersTuple || !(response.orderedHeaders instanceof List)) {
                throw error("INVALID_RESPONSE", "/headers", "Response ordered_headers must be a tuple of name/value pairs.");
            }
            List<String> result = new ArrayList<String>();
            for (Object item : asList(response.orderedHeaders)) {
                if (!(item instanceof List)) {
                    throw error("INVALID_RESPONSE", "/headers", "Response ordered_headers must be a tuple of name/value pairs.");
                }
                List<Object> pair = asList(item);
                if (pair.size() != 2 || !(pair.get(0) instanceof String) || !(pair.get(1) instanceof String)) {
                    throw error("INVALID_RESPONSE", "/headers", "Response ordered_headers must be a tuple of name/value pairs.");
                }
                if (((String) pair.get(0)).equalsIgnoreCase(name)) result.add((String) pair.get(1));
            }
            return result;
        }

        private static Object strictResponseJson(byte[] raw) throws ExecutionError {
            if (raw.length >= 3 && raw[0] == (byte) 0xef && raw[1] == (byte) 0xbb && raw[2] == (byte) 0xbf) {
                throw error("INVALID_RESPONSE_JSON", "/body", "Response body must be strict UTF-8 JSON.");
            }
            try {
                String text = StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
                        .decode(ByteBuffer.wrap(raw)).toString();
                return MiniJson.parse(text);
            } catch (CharacterCodingException | MiniJson.Error failure) {
                throw error("INVALID_RESPONSE_JSON", "/body", "Response body must be strict UTF-8 JSON.");
            }
        }

        private static boolean isAscii(String value) {
            for (int index = 0; index < value.length(); index++) if (value.charAt(index) > 0x7f) return false;
            return true;
        }
    }

    static final class BoundPath {
        final String value;
        final String owner;
        BoundPath(String value, String owner) { this.value = value; this.owner = owner; }
    }

    static final class QueryRow {
        final String name;
        final Object value;
        QueryRow(String name, Object value) { this.name = name; this.value = value; }
    }

    static final class BodyRow {
        final List<String> tokens;
        final Object value;
        final String owner;
        BodyRow(List<String> tokens, Object value, String owner) {
            this.tokens = tokens;
            this.value = value;
            this.owner = owner;
        }
    }

    static final class JsonNumber extends Number {
        private static final long serialVersionUID = 1L;
        final String raw;
        final BigDecimal decimal;
        final boolean integer;

        JsonNumber(String raw) {
            this.raw = raw;
            this.decimal = new BigDecimal(raw);
            this.integer = raw.indexOf('.') < 0 && raw.indexOf('e') < 0 && raw.indexOf('E') < 0;
        }

        @Override public int intValue() { return decimal.intValue(); }
        @Override public long longValue() { return decimal.longValue(); }
        @Override public float floatValue() { return decimal.floatValue(); }
        @Override public double doubleValue() { return decimal.doubleValue(); }
        @Override public String toString() { return raw; }
    }

    /** Strict duplicate-rejecting JSON reader and deterministic canonical writer. */
    static final class MiniJson {
        static final class Error extends Exception {
            private static final long serialVersionUID = 1L;
        }

        final String source;
        int index;

        MiniJson(String source) { this.source = source; }

        static Object parseBytes(byte[] raw) throws Error {
            if (raw.length >= 3 && raw[0] == (byte) 0xef && raw[1] == (byte) 0xbb && raw[2] == (byte) 0xbf) {
                throw new Error();
            }
            try {
                String decoded = StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
                        .decode(ByteBuffer.wrap(raw)).toString();
                return parse(decoded);
            } catch (CharacterCodingException failure) {
                throw new Error();
            }
        }

        static Object parse(String source) throws Error {
            MiniJson parser = new MiniJson(source);
            Object value = parser.value();
            parser.whitespace();
            if (parser.index != source.length()) throw new Error();
            return value;
        }

        Object value() throws Error {
            whitespace();
            if (index >= source.length()) throw new Error();
            char character = source.charAt(index);
            if (character == '{') return object();
            if (character == '[') return array();
            if (character == '"') return string();
            if (take("true")) return Boolean.TRUE;
            if (take("false")) return Boolean.FALSE;
            if (take("null")) return null;
            return number();
        }

        Map<String, Object> object() throws Error {
            index++;
            Map<String, Object> result = new LinkedHashMap<String, Object>();
            whitespace();
            if (peek('}')) { index++; return result; }
            while (true) {
                whitespace();
                if (!peek('"')) throw new Error();
                String key = string();
                whitespace();
                if (!peek(':')) throw new Error();
                index++;
                Object item = value();
                if (result.containsKey(key)) throw new Error();
                result.put(key, item);
                whitespace();
                if (peek('}')) { index++; return result; }
                if (!peek(',')) throw new Error();
                index++;
            }
        }

        List<Object> array() throws Error {
            index++;
            List<Object> result = new ArrayList<Object>();
            whitespace();
            if (peek(']')) { index++; return result; }
            while (true) {
                result.add(value());
                whitespace();
                if (peek(']')) { index++; return result; }
                if (!peek(',')) throw new Error();
                index++;
            }
        }

        String string() throws Error {
            if (!peek('"')) throw new Error();
            index++;
            StringBuilder result = new StringBuilder();
            while (index < source.length()) {
                char character = source.charAt(index++);
                if (character == '"') {
                    return result.toString();
                }
                if (character == '\\') {
                    if (index >= source.length()) throw new Error();
                    char escaped = source.charAt(index++);
                    if (escaped == '"' || escaped == '\\' || escaped == '/') result.append(escaped);
                    else if (escaped == 'b') result.append('\b');
                    else if (escaped == 'f') result.append('\f');
                    else if (escaped == 'n') result.append('\n');
                    else if (escaped == 'r') result.append('\r');
                    else if (escaped == 't') result.append('\t');
                    else if (escaped == 'u') {
                        if (index + 4 > source.length()) throw new Error();
                        int value = 0;
                        for (int cursor = 0; cursor < 4; cursor++) {
                            int digit = Character.digit(source.charAt(index + cursor), 16);
                            if (digit < 0) throw new Error();
                            value = value * 16 + digit;
                        }
                        result.append((char) value);
                        index += 4;
                    } else {
                        throw new Error();
                    }
                } else {
                    if (character < 0x20) throw new Error();
                    result.append(character);
                }
            }
            throw new Error();
        }

        JsonNumber number() throws Error {
            int start = index;
            if (peek('-')) index++;
            if (index >= source.length()) throw new Error();
            if (peek('0')) {
                index++;
            } else {
                if (!asciiDigit(current())) throw new Error();
                while (index < source.length() && asciiDigit(source.charAt(index))) index++;
            }
            if (peek('.')) {
                index++;
                if (index >= source.length() || !asciiDigit(source.charAt(index))) throw new Error();
                while (index < source.length() && asciiDigit(source.charAt(index))) index++;
            }
            if (peek('e') || peek('E')) {
                index++;
                if (peek('+') || peek('-')) index++;
                if (index >= source.length() || !asciiDigit(source.charAt(index))) throw new Error();
                while (index < source.length() && asciiDigit(source.charAt(index))) index++;
            }
            String token = source.substring(start, index);
            try {
                JsonNumber result = new JsonNumber(token);
                if (!result.integer && !Double.isFinite(result.doubleValue())) throw new Error();
                return result;
            } catch (NumberFormatException failure) {
                throw new Error();
            }
        }

        boolean take(String token) {
            if (source.startsWith(token, index)) {
                index += token.length();
                return true;
            }
            return false;
        }

        boolean peek(char expected) {
            return index < source.length() && source.charAt(index) == expected;
        }

        char current() throws Error {
            if (index >= source.length()) throw new Error();
            return source.charAt(index);
        }

        void whitespace() {
            while (index < source.length()) {
                char character = source.charAt(index);
                if (character != ' ' && character != '\t' && character != '\r' && character != '\n') break;
                index++;
            }
        }

        static boolean asciiDigit(char value) { return value >= '0' && value <= '9'; }

        static String canonical(Object value) throws Error {
            if (value == null) return "null";
            if (value instanceof String) return quote((String) value);
            if (value instanceof Boolean) return ((Boolean) value).booleanValue() ? "true" : "false";
            if (value instanceof JsonNumber) return canonicalNumber((JsonNumber) value);
            if (value instanceof List) {
                StringBuilder result = new StringBuilder("[");
                List<?> array = (List<?>) value;
                for (int index = 0; index < array.size(); index++) {
                    if (index > 0) result.append(',');
                    result.append(canonical(array.get(index)));
                }
                return result.append(']').toString();
            }
            if (value instanceof Map) {
                TreeMap<String, Object> sorted = new TreeMap<String, Object>();
                for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                    if (!(entry.getKey() instanceof String)) throw new Error();
                    sorted.put((String) entry.getKey(), entry.getValue());
                }
                StringBuilder result = new StringBuilder("{");
                int count = 0;
                for (Map.Entry<String, Object> entry : sorted.entrySet()) {
                    if (count++ > 0) result.append(',');
                    result.append(quote(entry.getKey())).append(':').append(canonical(entry.getValue()));
                }
                return result.append('}').toString();
            }
            throw new Error();
        }

        static String canonicalNumber(JsonNumber value) {
            if (value.integer) return value.decimal.toBigInteger().toString();
            return value.raw.replace('E', 'e');
        }

        static String quote(String value) {
            StringBuilder result = new StringBuilder("\"");
            for (int index = 0; index < value.length(); index++) {
                char character = value.charAt(index);
                if (character == '"' || character == '\\') result.append('\\').append(character);
                else if (character == '\b') result.append("\\b");
                else if (character == '\f') result.append("\\f");
                else if (character == '\n') result.append("\\n");
                else if (character == '\r') result.append("\\r");
                else if (character == '\t') result.append("\\t");
                else if (character < 0x20) result.append(String.format(Locale.ROOT, "\\u%04x", Integer.valueOf(character)));
                else result.append(character);
            }
            return result.append('"').toString();
        }
    }
}
