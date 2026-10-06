package net.javaguides.springboot.controller;

import net.javaguides.springboot.bean.Student;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.ArrayList;

@RestController
public class StudentController {
    //http://localhost:8080/student

    @GetMapping("student")
    public Student getStudent(){
        Student student =new Student(
                1,
                "Ramseh",
                "Mishra"
        );
        return student ;
    }
    //http://localhost:8080/students

@GetMapping("students")
    public List<Student> getStudents(){
        List<Student> students = new ArrayList<>();
        students.add(new Student(1,"Ramesh","Mishra"));
        students.add(new Student(2, "Umesh","Mishra"));
        students.add(new Student(3, "Ram","Mishra"));
        students.add(new Student(4, "Sanjay", "Mishra"));
        return students;
    }


    // Spring Boot REST API  with Path Variable
 // {id} -URI template variable
    // http://localhost:8080/students/1
    @GetMapping("students/{id}")
    public Student studentPathVariable(@PathVariable int id){

        return new Student(id,"Ramsesh","Mishra" );
    }

    // Spring boot REST API  with Request Param
    //http://localhost:8080/students/query?id=1

    @GetMapping("students/query")
    public Student studentRequestVariable(@RequestParam int id ){
         return new Student(id, "Ramesh", "Mishra");
    }

    // Spring Boot RESTAPI that handles HTTP POST Request ---Creating New Resource
    // @PostMapping and @RequestBody

    @PostMapping("students/create")
    @ResponseStatus(HttpStatus.CREATED)
    public Student createStudent( @RequestBody Student student){
        System.out.println(student.getId());
        System.out.println(student.getFirstName());
        System.out.println(student.getLastName());
        return student;
    }

    // Spring Boot REST API that handles  HTTP PUT Request ----- updating existing resource
    //@PutMapping
@PutMapping("students/{id}/update")
    public Student updateStudent(@RequestBody  Student student, @PathVariable int id){
        System.out.println(student.getFirstName());
        System.out.println(student.getLastName());
        return student;
    }
// Spring Boot REST API that handles  HTTP Delete Request -------- Delete existing recourse
    // byuing @DeleteMappping Annotation

    @DeleteMapping("students/{id}/delete")
     public  String deleteStudent(@PathVariable int id ){
         System.out.println(id);
        return  "Student Successfully Deleted!";
     }
}
