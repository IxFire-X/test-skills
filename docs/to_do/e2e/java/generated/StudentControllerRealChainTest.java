package net.javaguides.springboot.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import net.javaguides.springboot.bean.Student;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(StudentController.class)
class StudentControllerRealChainTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    @DisplayName("TC-0001: Single student endpoint returns the supported student")
    void shouldReturnSupportedSingleStudent() throws Exception {
        mockMvc.perform(get("/student"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(1))
                .andExpect(jsonPath("$.firstName").value("Ramseh"))
                .andExpect(jsonPath("$.lastName").value("Mishra"));
    }

    @Test
    @DisplayName("TC-0002: Student list endpoint returns four students")
    void shouldReturnFourStudents() throws Exception {
        mockMvc.perform(get("/students"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", hasSize(4)));
    }

    @Test
    @DisplayName("TC-0003: Student path endpoint echoes a numeric id")
    void shouldEchoPathId() throws Exception {
        mockMvc.perform(get("/students/7"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(7))
                .andExpect(jsonPath("$.firstName").value("Ramsesh"))
                .andExpect(jsonPath("$.lastName").value("Mishra"));
    }

    @Test
    @DisplayName("TC-0004: Student query endpoint echoes a numeric id")
    void shouldEchoQueryId() throws Exception {
        mockMvc.perform(get("/students/query").param("id", "3"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(3))
                .andExpect(jsonPath("$.firstName").value("Ramesh"))
                .andExpect(jsonPath("$.lastName").value("Mishra"));
    }

    @Test
    @DisplayName("TC-0005: Student creation echoes the request body")
    void shouldCreateAndEchoStudent() throws Exception {
        Student request = new Student(10, "Anna", "Smith");

        mockMvc.perform(post("/students/create")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value(10))
                .andExpect(jsonPath("$.firstName").value("Anna"))
                .andExpect(jsonPath("$.lastName").value("Smith"));
    }

    @Test
    @DisplayName("TC-0006: Student update echoes the request names")
    void shouldUpdateAndEchoStudentNames() throws Exception {
        Student request = new Student(5, "Updated", "Name");

        mockMvc.perform(put("/students/5/update")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.firstName").value("Updated"))
                .andExpect(jsonPath("$.lastName").value("Name"));
    }

    @Test
    @DisplayName("TC-0007: Student deletion returns the supported confirmation")
    void shouldDeleteStudent() throws Exception {
        mockMvc.perform(delete("/students/2/delete"))
                .andExpect(status().isOk())
                .andExpect(content().string("Student Successfully Deleted!"));
    }
}
