"""PQ4: the two rules of autotest_hints about the expectations of automated tests, read from code alone (no run).

``AT_SELF_COMPUTED`` — the expected value is the very call the test checks; ``AT_NO_STATE_CHECK`` — the test writes, expects a refusal and never looks at the state again.
Each rule has the cases it must find and the cases it must leave alone (the neighbours that are right), in Java (JUnit, AssertJ, Hamcrest, MockMvc, the generator's
own ``performRequest`` helper) and in pytest.
"""
from __future__ import annotations

from typing import Any

import pytest

from tools.autotest_hints import CODES, autotest_hints, capped_autotest_hints, counts


def _hints(text: str, language: str = "java") -> list[tuple[str, int, str]]:
    found = autotest_hints({"artifacts": {"generated_files": [{"file_id": "FILE-1", "language": language, "content": text}]}})
    return [(hint["code"], hint["line"], hint["rule"]) for hint in found if hint["code"] in ("AT_SELF_COMPUTED", "AT_NO_STATE_CHECK")]


def _line(text: str, needle: str) -> int:
    return next(number for number, line in enumerate(text.splitlines(), 1) if needle in line)


def test_the_two_codes_are_known_and_counted() -> None:
    assert CODES[-2:] == ("AT_SELF_COMPUTED", "AT_NO_STATE_CHECK")
    hints = [{"code": "AT_SELF_COMPUTED", "file_id": "F", "line": 1, "rule": "same-call"}, {"code": "AT_NO_STATE_CHECK", "file_id": "F", "line": 2, "rule": "rejected-without-state-check"}]
    assert counts(hints) == {"AT_SELF_COMPUTED": 1, "AT_NO_STATE_CHECK": 1}
    assert capped_autotest_hints(hints) == hints


# --- AT_SELF_COMPUTED, Java --------------------------------------------------------------------------------------------------------------------------

SELF_JAVA = '''class PriceTest {
    @Test
    void sameCallInJUnit() {
        assertEquals(service.price(item), service.price(item));
    }

    @Test
    void sameCallInAssertJ() {
        assertThat(calc.total(order)).isEqualTo(calc.total(order));
    }

    @Test
    void sameCallInHamcrest() {
        assertThat(calc.total(order), is(calc.total(order)));
    }

    @Test
    void expectedBuiltFromTheCall() {
        assertThat(calc.total(order)).isEqualTo(calc.total(order) * 2);
    }

    @Test
    void expectedVariableHoldsTheCall() {
        Money expected = calc.total(order);
        assertThat(calc.total(order)).isEqualTo(expected);
    }

    @Test
    void junit4MessageFirst() {
        assertEquals("price", service.price(item), service.price(item));
    }
}
'''


@pytest.mark.parametrize("needle", [
    "assertEquals(service.price(item), service.price(item));", "isEqualTo(calc.total(order));", "is(calc.total(order))", "isEqualTo(calc.total(order) * 2)",
    "assertThat(calc.total(order)).isEqualTo(expected)", "assertEquals(\"price\"",
])
def test_an_expected_value_that_is_the_checked_call_is_found(needle: str) -> None:
    line = _line(SELF_JAVA, needle)
    assert ("AT_SELF_COMPUTED", line, "same-call") in _hints(SELF_JAVA), (needle, _hints(SELF_JAVA))


def test_exactly_those_six_are_found() -> None:
    assert len([hint for hint in _hints(SELF_JAVA) if hint[0] == "AT_SELF_COMPUTED"]) == 6


FINE_JAVA = '''class PriceTest {
    @Test
    void aLiteral() {
        assertEquals(42, service.price(item));
        assertThat(service.price(item)).isEqualTo(42);
    }

    @Test
    void anotherArgument() {
        assertThat(service.price(a)).isEqualTo(service.price(b));
        assertEquals(service.price(a, 1), service.price(a, 2));
    }

    @Test
    void anotherMethod() {
        assertEquals(service.price(item), service.priceWithTax(item) - service.tax(item));
    }

    @Test
    void anotherReceiver() {
        assertEquals(first.total(order), second.total(order));
    }

    @Test
    void theValueObjectsAreMadeTwiceToCompareThem() {
        assertEquals(ImmutablePair.of(1, "a"), ImmutablePair.of(1, "a"));
        assertThat(Money.valueOf(5)).isEqualTo(Money.valueOf(5));
        assertEquals(new Owner("a"), new Owner("a"));
    }

    @Test
    void theContractMethods() {
        assertEquals(entry.hashCode(), entry.hashCode());
        assertEquals(entry.toString(), entry.toString());
    }

    @Test
    void theIdentityOfTheSameCall() {
        assertSame(matcher, StrMatcher.trimMatcher());
        assertThat(StrMatcher.trimMatcher()).isSameAs(StrMatcher.trimMatcher());
    }

    @Test
    void aCastIsNotACall() {
        assertEquals((short) 0, (short) 0);
    }

    @Test
    void beforeAndAfterOfAStateThatChanged() {
        long before = repository.count();
        repository.save(owner);
        assertThat(repository.count()).isEqualTo(before + 1);
    }

    @Test
    void theStateReadBeforeAndAfterARefusedWrite() {
        int expectedBefore = repository.count();
        assertThrows(IllegalStateException.class, () -> repository.save(null));
        assertEquals(expectedBefore, repository.count());
    }

    @Test
    void aVariableThatIsNotNamedExpected() {
        String text = body.text();
        assertEquals(text, body.text());
    }
}
'''


def test_the_neighbours_that_are_right_are_left_alone() -> None:
    assert [hint for hint in _hints(FINE_JAVA) if hint[0] == "AT_SELF_COMPUTED"] == []


def test_the_same_accessor_is_its_own_rule() -> None:
    text = "class T {\n    @Test\n    void t() {\n        assertEquals(owner.getName(), owner.getName());\n    }\n}\n"
    assert _hints(text) == [("AT_SELF_COMPUTED", 4, "same-accessor")]


def test_a_very_long_assertion_is_not_read_to_the_end_of_the_file() -> None:
    text = "class T {\n    @Test\n    void t() {\n        assertEquals(" + "f(" * 5000 + "x" + ")" * 5000 + ");\n    }\n}\n"
    assert _hints(text) == []  # bounded: an assertion longer than the window is not read


# --- AT_NO_STATE_CHECK, Java ----------------------------------------------------------------------------------------------------------------------------

NEGATIVE_JAVA = '''class OwnerControllerTest {
    @Test
    void rejectsWithAStatusAndReadsNothing() throws Exception {
        mockMvc.perform(post("/owners/new").param("firstName", ""))
            .andExpect(status().isBadRequest());
    }

    @Test
    void rejectsAndReadsTheStateBack() throws Exception {
        long before = ownerRepository.count();
        mockMvc.perform(post("/owners/new").param("firstName", "")).andExpect(status().isBadRequest());
        assertThat(ownerRepository.count()).isEqualTo(before);
    }

    @Test
    void rejectsAndVerifiesNothingWasSaved() throws Exception {
        mockMvc.perform(post("/owners/new").param("firstName", "")).andExpect(status().isBadRequest());
        verify(ownerRepository, never()).save(any());
    }

    @Test
    void rejectsAndReadsItAgainOverHttp() throws Exception {
        mockMvc.perform(put("/owners/1").param("firstName", "")).andExpect(status().is4xxClientError());
        mockMvc.perform(get("/owners/1")).andExpect(status().isOk());
    }

    @Test
    void anExceptionFromTheServiceAndNothingAfter() {
        assertThrows(IllegalStateException.class, () -> ownerService.save(null));
    }

    @Test
    void anExceptionFromTheServiceAndTheStateAfter() {
        assertThrows(IllegalStateException.class, () -> ownerService.save(null));
        assertThat(ownerRepository.findAll()).isEmpty();
    }

    @Test
    void aFormWithErrorsAndNothingAfter() throws Exception {
        mockMvc.perform(post("/owners/new").param("firstName", ""))
            .andExpect(model().attributeHasFieldErrors("owner", "firstName"));
    }

    @Test
    void onlyAReadIsRefused() throws Exception {
        mockMvc.perform(get("/owners/999")).andExpect(status().isNotFound());
    }

    @Test
    void aSuccessIsNotARefusal() throws Exception {
        mockMvc.perform(post("/owners/new").param("firstName", "Ann")).andExpect(status().is3xxRedirection());
    }

    @Test
    void aPureFunctionThatThrows() {
        assertThrows(IllegalArgumentException.class, () -> Validator.check(null));
    }
}
'''


@pytest.mark.parametrize("name,flagged", [
    ("rejectsWithAStatusAndReadsNothing", True), ("rejectsAndReadsTheStateBack", False), ("rejectsAndVerifiesNothingWasSaved", False),
    ("rejectsAndReadsItAgainOverHttp", False), ("anExceptionFromTheServiceAndNothingAfter", True), ("anExceptionFromTheServiceAndTheStateAfter", False),
    ("aFormWithErrorsAndNothingAfter", True), ("onlyAReadIsRefused", False), ("aSuccessIsNotARefusal", False), ("aPureFunctionThatThrows", False),
])
def test_a_rejected_write_is_flagged_only_when_nothing_reads_the_state_after_it(name: str, flagged: bool) -> None:
    start = _line(NEGATIVE_JAVA, f"void {name}(")
    following = [number for number in (_line(NEGATIVE_JAVA, f"void {other}(") for other in ("rejectsAndReadsTheStateBack",)) if number > start]
    lines = NEGATIVE_JAVA.splitlines()
    end = next((number for number in range(start, len(lines)) if lines[number].startswith("    }")), len(lines))
    inside = [line for code, line, rule in _hints(NEGATIVE_JAVA) if code == "AT_NO_STATE_CHECK" and start < line <= end + 1]
    assert bool(inside) is flagged, (name, _hints(NEGATIVE_JAVA))
    assert following is not None


GENERATED_STYLE = '''class OwnerLifecycleFlowTests {
    @Test
    void tc_B1_004() throws Exception {
        runWithCleanup(() -> {
            Result s1 = countOwners("");
            int s1_ownersBefore = s1.integer("owner_count");
            Result s2 = performRequest("POST", "/owners/new", Map.of("firstName", ""), null);
            assertThat(s2.integer("status")).as("ASSERT-B1-004-2-1-1").isEqualTo(200);
            assertThat(s2.str("field_error_fields")).as("ASSERT-B1-004-2-1-3").isEqualTo("firstName");
            Result s3 = countOwners("");
            assertThat(s3.integer("owner_count")).as("ASSERT-B1-004-3-1-1").isEqualTo(s1_ownersBefore);
        });
    }

    @Test
    void tc_B1_005() throws Exception {
        runWithCleanup(() -> {
            Result s2 = performRequest("POST", "/owners/new", Map.of("firstName", "   "), null);
            assertThat(s2.integer("status")).as("ASSERT-B1-005-2-1-1").isEqualTo(200);
            assertThat(s2.str("field_error_fields")).as("ASSERT-B1-005-2-1-3").isEqualTo("firstName");
        });
    }

    @Test
    void tc_B1_006() throws Exception {
        runWithCleanup(() -> {
            Result s2 = performRequest("DELETE", "/owners/{ownerId}", Map.of(), 99999999);
            assertThat(s2.integer("status")).as("ASSERT-B1-006-2-1-1").isEqualTo(404);
            Result s3 = performRequest("GET", "/owners", Map.of("lastName", "x"), null);
            assertThat(s3.integer("status")).as("ASSERT-B1-006-3-1-1").isEqualTo(200);
        });
    }
}
'''


def test_the_generators_own_style_is_read_the_same_way() -> None:
    """A POST through the ``performRequest`` helper, the refusal as a status or as the form's field errors, the state read back through ``countOwners`` or a GET."""
    flagged = [line for code, line, rule in _hints(GENERATED_STYLE) if code == "AT_NO_STATE_CHECK"]
    assert flagged == [_line(GENERATED_STYLE, "ASSERT-B1-005-2-1-3")]


# --- the Python side -------------------------------------------------------------------------------------------------------------------------------

PY = '''def test_rejects_and_reads_nothing(client):
    resp = client.post("/owners", json={"name": ""})
    assert resp.status_code == 400


def test_rejects_and_reads_back(client):
    before = client.get("/owners").json()
    resp = client.post("/owners", json={"name": ""})
    assert resp.status_code == 422
    assert client.get("/owners").json() == before


def test_raises_and_reads_nothing(session):
    with pytest.raises(ValueError):
        create_owner(session, name="")


def test_raises_and_counts_after(session):
    with pytest.raises(ValueError):
        create_owner(session, name="")
    assert session.query(Owner).count() == 0


def test_a_pure_function_raises():
    with pytest.raises(ValueError):
        parse("x")


def test_same_call(service):
    assert service.price(item) == service.price(item)


def test_expected_variable(calc):
    expected = calc.total(order)
    assert calc.total(order) == expected


def test_assert_equal_method(self):
    self.assertEqual(service.price(item), service.price(item))


def test_fine(service):
    assert service.price(item) == 42
    assert service.price(a) == service.price(b)
    assert entry.hashCode() == entry.hashCode()
    assert Money.of(5) == Money.of(5)
'''


def test_pytest_functions_are_read_by_the_same_two_rules() -> None:
    found = _hints(PY, "python")
    assert [line for code, line, rule in found if code == "AT_NO_STATE_CHECK"] == [_line(PY, 'assert resp.status_code == 400'), _line(PY, "with pytest.raises(ValueError):")]
    assert [line for code, line, rule in found if code == "AT_SELF_COMPUTED"] == [
        _line(PY, "assert service.price(item) == service.price(item)"), _line(PY, "assert calc.total(order) == expected"), _line(PY, "self.assertEqual(service.price(item)")]


def test_the_hints_name_the_file_and_the_line_and_never_the_code() -> None:
    found = autotest_hints({"artifacts": {"generated_files": [{"file_id": "FILE-9", "language": "java", "content": SELF_JAVA}]}})
    new = [hint for hint in found if hint["code"] == "AT_SELF_COMPUTED"]
    assert new and all(set(hint) <= {"code", "file_id", "line", "rule", "assertion_id"} and hint["file_id"] == "FILE-9" for hint in new)
    assert "service.price" not in str(new)


def test_the_assertion_id_on_the_line_is_named() -> None:
    text = 'class T {\n    @Test\n    void t() {\n        assertThat(calc.total(order)).as("ASSERT-1-1-1").isEqualTo(calc.total(order));\n    }\n}\n'
    found: list[dict[str, Any]] = autotest_hints({"artifacts": {"generated_files": [{"file_id": "F", "language": "java", "content": text}]}})
    assert [hint.get("assertion_id") for hint in found if hint["code"] == "AT_SELF_COMPUTED"] == ["ASSERT-1-1-1"]


def test_a_test_method_that_is_cut_off_is_not_judged_for_what_it_expects() -> None:
    text = 'class T {\n    @Test\n    void t() {\n        mockMvc.perform(post("/owners")).andExpect(status().isBadRequest());\n'
    assert _hints(text) == []
    assert _hints(text + "    }\n}\n") == [("AT_NO_STATE_CHECK", 4, "rejected-without-state-check")]
