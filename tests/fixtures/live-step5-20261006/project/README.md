# Spring Boot REST API  

## Overview  
This is a simple **Spring Boot REST API** project built using **Spring Boot 3.4.2** and **Maven**.  
It demonstrates the basic setup of a REST API with Spring Boot.  

## Features  
- RESTful API endpoints  
- Uses **Spring Boot Starter Web**  
- Built with **Maven**  
- Tested with **Spring Boot Starter Test**  
- Java **17**  

## Project Structure  
```
springboot-rest-api/  
│-- src/  
│   ├── main/  
│   │   ├── java/net/javaguides/springboot/  
│   │   │   ├── SpringbootRestApiApplication.java  
│   │   │   ├── controller/  
│   │   │   ├── service/  
│   │   │   ├── model/  
│   │   ├── resources/  
│   │       ├── application.properties  
│   ├── test/  
│       ├── java/net/javaguides/springboot/  
│       │   ├── SpringbootRestApiApplicationTests.java  
│-- pom.xml  
│-- .gitignore  
│-- README.md  
```  

## Prerequisites  
- **Java 17** installed  
- **Maven** installed  
- **Git** installed  

## Setup & Installation  
1. Clone the repository:  
   ```
   git clone https://github.com/your-username/springboot-rest-api.git  
   cd springboot-rest-api  
   ```  

2. Build the project using Maven:  
   ```
   mvn clean install  
   ```  

3. Run the Spring Boot application:  
   ```
   mvn spring-boot:run  
   ```  

4. The API will be available at:  
   ```
   http://localhost:8080  
   ```  

## API Endpoints  
| Method | Endpoint       | Description              |  
|--------|--------------|--------------------------|  
| GET    | `/api/hello`  | Returns a welcome message |  

## Running Tests  
Run the unit tests with:  
```
mvn test  
```  

## Resources  
- [Spring Boot Documentation](https://docs.spring.io/spring-boot/docs/current/reference/html/)  
- [Maven Documentation](https://maven.apache.org/guides/index.html)  

---

## Next Steps  
- Add authentication (JWT)  
- Implement database integration with **Spring Data JPA**  
- Deploy the app to **AWS/GCP**  
