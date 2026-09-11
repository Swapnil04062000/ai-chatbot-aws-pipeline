pipeline {
    agent any
    
    environment {
        AWS_ACCOUNT_ID = '344367179698' 
        AWS_REGION     = 'us-east-1'
        ECR_REPO_NAME  = 'ai-chatbot-repo'
        IMAGE_TAG      = "build-${BUILD_NUMBER}"
        ECR_REGISTRY   = "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
    }
    
    stages {
        stage('Source Checkout') {
            steps {
                echo 'Pulling code modifications from GitHub SCM...'
                checkout scm
            }
        }
        
        stage('Docker Image Build') {
            steps {
                echo 'Compiling project boundaries using multi-stage Docker & Astral uv...'
                script {
                    dockerImage = docker.build("${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG}")
                    sh "docker tag ${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG} ${ECR_REGISTRY}/${ECR_REPO_NAME}:latest"
                }
            }
        }
        
        stage('Artifact Registry Push') {
            steps {
                echo 'Logging into AWS ECR using Instance Profile and pushing container images...'
                script {
                    // Force a secure login token generation using the server's IAM role
                    sh "aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REGISTRY}"
                    
                    // Execute the clean push commands directly
                    sh "docker push ${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG}"
                    sh "docker push ${ECR_REGISTRY}/${ECR_REPO_NAME}:latest"
                }
            }
        }
    }
        
    
    post {
        success {
            echo 'CI/CD Workflow pipeline executed successfully! Image pushed to AWS ECR.'
        }
        failure {
            echo 'Build pipeline failure detected. Inspect step tracing outputs.'
        }
    }
}
